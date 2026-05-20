from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
import json
import subprocess
import time
from typing import Any, Protocol

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    IngestionArtifactType,
    IngestionStatus,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.schemas.ingestion import (
    CliIngestionResponse,
    CliIngestionSummary,
    IngestionRun,
    RawIngestionArtifact,
)
from app.services.contract_paths import contract_path
from app.services.claim_store import (
    ClaimStore,
    DuplicateClaimError,
    DuplicateEvidenceError,
    EvidencePolicyError,
    ToolNotAllowedError,
)
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_ingestion_artifact_id,
    generate_ingestion_run_id,
)
from app.services.orchestrator import CanonOrchestrator
from app.services.risk_classifier import classify_action


_INGESTION_CONTRACT = contract_path("cli_ingestion_sources.v1.json")
_READ_ONLY_MARKERS = frozenset({"--help", "-h", "--version", "help"})
# Shell metacharacters and control bytes that must never appear inside any argv element.
# Matching is element-wise substring containment because a single argv string of the form
# "foo;rm -rf /" would be a single shell-control payload to the receiving binary if the
# binary ever re-shelled its arguments.
_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "|", ";", "&", "&&", "||", ">", "<", ">>", "$(", "`", "\\n", "\\r", "\n", "\r",
)
# Binaries that are destructive even when invoked with --help, because their first
# positional argument can be interpreted as a target (e.g. `rm -rf / --help` treats
# `--help` as a path). These are blocked unconditionally as the binary itself.
_DESTRUCTIVE_BINARIES: frozenset[str] = frozenset(
    {
        "rm", "rmdir", "unlink",
        "dd", "mkfs", "shred", "wipe",
        "kill", "killall", "pkill",
        "shutdown", "halt", "reboot", "poweroff",
        "chmod", "chown", "chroot",
    }
)


class CliIngestionError(ValueError):
    """Raised when a CLI ingestion request is unsupported or unsafe."""


@dataclass(frozen=True)
class CliCaptureSpec:
    tool_id: str
    command: str
    claim_type: ClaimType
    capture_argv: tuple[str, ...]
    evidence_type: EvidenceType
    max_output_chars: int
    timeout_seconds: int


@dataclass(frozen=True)
class CliRunResult:
    argv: tuple[str, ...]
    returncode: int
    output: str


class CliRunner(Protocol):
    def run(self, spec: CliCaptureSpec) -> CliRunResult:
        """Run a safe capture command and return captured output."""


class SafeCliRunner:
    def run(self, spec: CliCaptureSpec) -> CliRunResult:
        _assert_safe_capture_argv(spec.capture_argv)
        try:
            proc = subprocess.Popen(
                list(spec.capture_argv),
                cwd="/",
                env={},
                shell=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
        except OSError as exc:
            raise CliIngestionError(f"CLI capture failed: {exc}") from exc

        deadline = time.monotonic() + spec.timeout_seconds
        max_chars = spec.max_output_chars
        chunks: list[str] = []
        total = 0
        try:
            assert proc.stdout is not None  # for type checkers; we set stdout=PIPE
            while True:
                if time.monotonic() > deadline:
                    _terminate(proc)
                    raise CliIngestionError(
                        f"CLI capture timed out after {spec.timeout_seconds} seconds."
                    )
                # Read at most one chunk beyond the cap so we can detect overflow.
                read_size = min(4096, max_chars - total + 1)
                if read_size <= 0:
                    _terminate(proc)
                    raise CliIngestionError(
                        f"CLI capture exceeded max_output_chars={max_chars}."
                    )
                try:
                    chunk = proc.stdout.read(read_size)
                except OSError as exc:
                    _terminate(proc)
                    raise CliIngestionError(f"CLI capture failed: {exc}") from exc
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_chars:
                    _terminate(proc)
                    raise CliIngestionError(
                        f"CLI capture exceeded max_output_chars={max_chars}."
                    )
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                _terminate(proc)
                raise CliIngestionError(
                    f"CLI capture timed out after {spec.timeout_seconds} seconds."
                ) from exc
        finally:
            if proc.stdout is not None:
                proc.stdout.close()

        output = "".join(chunks)
        if not output.strip():
            raise CliIngestionError("CLI capture produced no output.")
        return CliRunResult(
            argv=tuple(spec.capture_argv),
            returncode=proc.returncode if proc.returncode is not None else 0,
            output=output,
        )


class CliIngestionService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        runner: CliRunner | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        self._runner = runner or SafeCliRunner()
        self._now = now

    def ingest(
        self,
        *,
        tool_id: str,
        command: str,
        submitted_by: str = "cli-ingestion-agent",
        verify: bool = True,
    ) -> CliIngestionResponse:
        spec = cli_capture_spec(tool_id=tool_id, command=command)
        run_id = generate_ingestion_run_id()
        started_at = self._timestamp()
        run = IngestionRun(
            run_id=run_id,
            tool_id=spec.tool_id,
            command=spec.command,
            status=IngestionStatus.IN_PROGRESS,
            submitted_by=submitted_by,
            created_at=started_at,
            completed_at=None,
            summary=CliIngestionSummary(capture_argv=list(spec.capture_argv)),
        )
        self._store.save_ingestion_run(run)

        errors: list[str] = []
        artifact_ids: list[str] = []
        created_claim_ids: list[str] = []
        verification_result_ids: list[str] = []

        try:
            result = self._runner.run(spec)
            captured_at = self._timestamp()
            artifact = _artifact_from_capture(
                run_id=run_id,
                result=result,
                captured_at=captured_at,
            )
            self._store.save_ingestion_artifact(artifact)
            artifact_ids.append(artifact.artifact_id)

            claim = _claim_from_capture(
                spec=spec,
                artifact=artifact,
                submitted_by=submitted_by,
                created_at=captured_at,
            )
            self._store.create(claim)
            created_claim_ids.append(claim.claim_id)

            if verify:
                verification = CanonOrchestrator(self._store).verify_claim(claim)
                saved = self._store.save_verification_result(verification)
                verification_result_ids.append(saved.verification_id)
        except (
            CliIngestionError,
            DuplicateClaimError,
            DuplicateEvidenceError,
            EvidencePolicyError,
            ToolNotAllowedError,
            ValueError,
        ) as exc:
            errors.append(str(exc))

        completed_at = self._timestamp()
        status = IngestionStatus.COMPLETED if not errors else IngestionStatus.FAILED
        summary = CliIngestionSummary(
            capture_argv=list(spec.capture_argv),
            created_claim_ids=created_claim_ids,
            verification_result_ids=verification_result_ids,
            artifact_ids=artifact_ids,
            errors=errors,
        )
        self._store.save_ingestion_run(
            run.model_copy(
                update={
                    "status": status,
                    "completed_at": completed_at,
                    "summary": summary,
                }
            )
        )
        return CliIngestionResponse(
            run_id=run_id,
            status=status,
            created_claim_ids=created_claim_ids,
            verification_result_ids=verification_result_ids,
            artifact_ids=artifact_ids,
            errors=errors,
        )

    def ingest_all_for_tool(
        self,
        *,
        tool_id: str,
        submitted_by: str = "cli-ingestion-agent",
        verify: bool = True,
    ) -> list[CliIngestionResponse]:
        commands = list_cli_commands_for_tool(tool_id)
        if not commands:
            raise CliIngestionError(
                f"CLI ingestion is not allowlisted for tool '{tool_id}'."
            )
        responses: list[CliIngestionResponse] = []
        for command in commands:
            responses.append(
                self.ingest(
                    tool_id=tool_id,
                    command=command,
                    submitted_by=submitted_by,
                    verify=verify,
                )
            )
        return responses

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)


def list_cli_commands_for_tool(tool_id: str) -> list[str]:
    """Return the canonical command strings allowlisted for a tool, in contract order.

    Returns an empty list if the tool is not in the contract. Callers wanting
    a hard error should call `cli_capture_spec` for a known command instead.
    """
    normalized_tool_id = tool_id.strip().casefold()
    data = _cli_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        return []
    return [str(item["command"]) for item in tool["captures"]]


def cli_capture_spec(*, tool_id: str, command: str) -> CliCaptureSpec:
    normalized_tool_id = tool_id.strip().casefold()
    normalized_command = " ".join(command.strip().split()).casefold()
    data = _cli_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        raise CliIngestionError(f"CLI ingestion is not allowlisted for tool '{tool_id}'.")

    for item in tool["captures"]:
        if item["command"].casefold() != normalized_command:
            continue
        capture_argv = tuple(str(part) for part in item["capture_argv"])
        _assert_safe_capture_argv(capture_argv)
        return CliCaptureSpec(
            tool_id=normalized_tool_id,
            command=item["command"],
            claim_type=ClaimType(item["claim_type"]),
            capture_argv=capture_argv,
            evidence_type=EvidenceType(item["evidence_type"]),
            max_output_chars=int(item["max_output_chars"]),
            timeout_seconds=int(item.get("timeout_seconds", data["default_timeout_seconds"])),
        )

    raise CliIngestionError(
        f"Command '{command}' is not allowlisted for CLI ingestion tool '{tool_id}'."
    )


@cache
def _cli_ingestion_sources() -> dict[str, Any]:
    with _INGESTION_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_cli_ingestion_sources(data)
    return data


def _validate_cli_ingestion_sources(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("CLI ingestion source contract must have version=1")
    timeout = data.get("default_timeout_seconds")
    if not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("CLI ingestion source contract must define a positive default timeout")
    tools = data.get("tools")
    if not isinstance(tools, dict) or not tools:
        raise ValueError("CLI ingestion source contract must define tools")

    for tool_id, tool in tools.items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("CLI ingestion source contract has invalid tool id")
        binary = tool.get("binary")
        if binary is not None and (not isinstance(binary, str) or not binary):
            raise ValueError(f"CLI ingestion tool '{tool_id}' has invalid binary")
        captures = tool.get("captures")
        if not isinstance(captures, list) or not captures:
            raise ValueError(f"CLI ingestion tool '{tool_id}' must define captures")
        for capture in captures:
            _validate_capture(tool_id, capture, binary)


def _validate_capture(tool_id: str, capture: dict[str, Any], binary: str | None) -> None:
    for key in ("command", "claim_type", "capture_argv", "evidence_type", "max_output_chars"):
        if key not in capture:
            raise ValueError(f"CLI ingestion capture for '{tool_id}' missing {key}")
    ClaimType(capture["claim_type"])
    EvidenceType(capture["evidence_type"])
    max_output_chars = capture["max_output_chars"]
    if not isinstance(max_output_chars, int) or max_output_chars <= 0 or max_output_chars > 8000:
        raise ValueError("CLI ingestion max_output_chars must be between 1 and 8000")
    argv = capture["capture_argv"]
    if not isinstance(argv, list) or not all(isinstance(part, str) and part for part in argv):
        raise ValueError(f"CLI ingestion capture for '{tool_id}' has invalid argv")
    if binary is not None and argv and argv[0] != binary:
        raise ValueError(
            f"CLI ingestion capture for '{tool_id}' uses argv[0]={argv[0]!r} but the tool "
            f"binary is declared as {binary!r}; argv[0] and binary must agree."
        )
    _assert_safe_capture_argv(tuple(argv))


def _terminate(proc: subprocess.Popen) -> None:
    """Best-effort terminate + reap. Swallows post-kill wait timeouts."""
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _assert_safe_capture_argv(argv: tuple[str, ...]) -> None:
    """Positive shape allowlist for ingestion argv.

    Required shape: ``<binary> <subcommand>* <read-only marker>`` where
    every subcommand is a non-flag token and the trailing element is one
    of ``--help / -h / --version / help``. The binary itself must not be
    a destructive command whose first positional argument can be misread
    as a target (for example ``rm -rf / --help``).

    This is defense-in-depth: the allowlist contract is the actual gate,
    but this guard runs at every contract load, every spec lookup, and
    every runner invocation so that a careless contract edit cannot
    quietly enable an unsafe capture.
    """

    if not argv:
        raise CliIngestionError("CLI capture argv must not be empty.")

    # Last token must be a read-only marker.
    if argv[-1] not in _READ_ONLY_MARKERS:
        raise CliIngestionError(
            "CLI capture argv must end with one of: --help, -h, --version, help."
        )

    # Forbidden shell-control characters anywhere in any element.
    for part in argv:
        for token in _FORBIDDEN_TOKENS:
            if token in part:
                raise CliIngestionError(
                    f"CLI capture argv element {part!r} contains forbidden token {token!r}."
                )

    # Tokens between the binary and the trailing marker must be subcommands,
    # not flags. This blocks ('rm', '-rf', '/', '--help') because '-rf'
    # is a flag and '/' is a target argument, not a subcommand.
    for token in argv[1:-1]:
        if token.startswith("-"):
            raise CliIngestionError(
                f"CLI capture argv element {token!r} looks like a flag; only "
                "subcommands and the final read-only marker are allowed."
            )

    # The binary itself must not be a destructive command.
    if argv[0].lower() in _DESTRUCTIVE_BINARIES:
        raise CliIngestionError(
            f"CLI capture binary {argv[0]!r} is destructive even with a read-only marker."
        )


def _artifact_from_capture(
    *,
    run_id: str,
    result: CliRunResult,
    captured_at: datetime,
) -> RawIngestionArtifact:
    artifact_id = generate_ingestion_artifact_id()
    source_uri = f"ayiru://ingestion/{run_id}/artifacts/{artifact_id}"
    raw_content = result.output
    return RawIngestionArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=IngestionArtifactType.CLI_OUTPUT,
        source_uri=source_uri,
        raw_content=raw_content,
        hash=f"sha256:{sha256(raw_content.encode()).hexdigest()}",
        captured_at=captured_at,
    )


_STATEMENT_MAX_CHARS = 480


def _statement_from_output(output: str, *, command: str) -> str:
    """Use the first non-empty line of captured output as the claim statement.

    Falls back to a minimal "<command>: (no descriptive output)" line if the
    output has no non-whitespace line — empty output is already rejected by
    the runner, so this fallback is only reached if the output is all
    whitespace through some unusual code path.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_STATEMENT_MAX_CHARS]
    return f"{command}: (no descriptive output)"[:_STATEMENT_MAX_CHARS]


def _claim_from_capture(
    *,
    spec: CliCaptureSpec,
    artifact: RawIngestionArtifact,
    submitted_by: str,
    created_at: datetime,
) -> KnowledgeClaim:
    risk = classify_action(spec.command, tool_id=spec.tool_id).risk_level
    evidence = Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=spec.evidence_type,
        source_uri=artifact.source_uri,
        excerpt=artifact.raw_content[:8000],
        hash=artifact.hash,
        captured_at=artifact.captured_at,
        trust_level=TrustLevel.MEDIUM,
    )
    return KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=spec.claim_type,
        subject=spec.command,
        statement=_statement_from_output(artifact.raw_content, command=spec.command),
        tool_id=spec.tool_id,
        submitted_by=submitted_by,
        evidence=[evidence],
        risk_level=risk,
        created_at=created_at,
    )
