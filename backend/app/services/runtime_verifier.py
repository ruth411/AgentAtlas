"""Stage 8: Runtime verification layer.

This module promotes claims from `L2_source_verified` to
`L3_runtime_verified` by actually running a safe, deterministic check
against the asserted behaviour and persisting the captured output as
durable audit evidence.

Today we support five claim types:

- `tool_exists`          — spawn `<tool> --version`, expect exit 0.
- `cli_command_exists`   — spawn `<tool> --version` (existence proxy; we do
                           NOT spawn arbitrary subcommands).
- `cli_flag_exists`      — spawn `<tool> --help`, grep the captured help
                           text for the asserted flag token.
- `api_endpoint_exists`  — issue a single HTTP HEAD to the asserted URL via
                           the shared SSRF guard; promote on an accepted
                           status code.
- `mcp_tool_exists`      — re-spawn the originating MCP server using the
                           Stage 7d runner, call `tools/list`, confirm the
                           tool name still appears.

Every other claim type (`side_effect`, `destructive_action`, `auth_requirement`,
`feature_deprecated`, `workflow_step`, `config_field_exists`) returns
`skipped=True` with a reason — we never invoke a destructive command to
"verify" it, and existence of a config field isn't observable without an
in-product schema fetch we've already done at ingest time.

The trust boundary: spawns are gated by a positive-shape argv allowlist;
only `allowed_commands` may be invoked; wall-clock and byte caps apply to
every subprocess run; HTTP HEAD requests go through the shared SSRF guard
with the tool's `official_hosts` as the allowlist. The captured
stdout/stderr/exit code is persisted as a `sandbox_execution_log`
`RawIngestionArtifact` so an auditor can replay the check.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceComponent
from app.schemas.enums import (
    ClaimType,
    ConfidenceBand,
    EvidenceType,
    IngestionArtifactType,
    OrchestratorDecision,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.schemas.ingestion import RawIngestionArtifact
from app.schemas.risk import RiskAssessment
from app.schemas.verification import (
    RuntimeVerificationResponse,
    VerificationResult,
)
from app.services.claim_store import ClaimStore
from app.services.confidence_scorer import (
    band_for_score,
    compute_confidence_breakdown,
)
from app.services.evidence_trust import _trust_sources
from app.services.http_safety import (
    HttpFetchError,
    resolve_url_for_safe_fetch,
    safe_https_request,
)
from app.services.ids import (
    generate_evidence_id,
    generate_ingestion_artifact_id,
    generate_verification_id,
)
from app.services.mcp_ingestion import (
    McpIngestionError,
    McpServerRunner,
    SafeMcpServerRunner,
    resolve_mcp_server_spec,
)
from app.services.contract_paths import contract_path
from app.services.risk_classifier import classify_action


_RUNTIME_CONTRACT = contract_path("runtime_verification_sources.v1.json")

_RUNTIME_PROMOTION_BONUS = 0.10
_RUNTIME_FAIL_PENALTY = 0.20
_REASON_MAX_CHARS = 480


class RuntimeVerificationError(ValueError):
    """Raised when a runtime verification request is unsupported / unsafe."""


# -------- Sandbox abstraction --------


@dataclass(frozen=True)
class SandboxSpec:
    command: str
    argv: tuple[str, ...]
    timeout_seconds: int
    max_bytes: int
    cwd: str | None = None
    env_passthrough: tuple[str, ...] = ("PATH", "HOME")


@dataclass(frozen=True)
class SandboxRunResult:
    command: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    started_at: datetime
    completed_at: datetime
    timed_out: bool = False
    truncated: bool = False


class SandboxRunner(Protocol):
    """Spawn a single command, capture stdout / stderr / exit code under
    strict caps. Implementations MUST honour `timeout_seconds` and
    `max_bytes` and MUST kill the process on cleanup."""

    def run(self, spec: SandboxSpec) -> SandboxRunResult:
        ...


class SubprocessSandboxRunner:
    """Production runner. Same safety shape as Stage 7a / 7d runners:

    - Resolves `command` via `PATH` and rejects anything not in
      `allowed_commands`.
    - Spawns with scrubbed env (only PATH + HOME inherited).
    - Polls stdout / stderr under a wall-clock deadline.
    - Caps cumulative bytes; truncates on overflow rather than crashing.
    - Always terminates the process before returning.
    """

    def run(self, spec: SandboxSpec) -> SandboxRunResult:
        if spec.command not in _allowed_commands():
            raise RuntimeVerificationError(
                f"Spawn command {spec.command!r} is not in allowed_commands."
            )
        resolved = shutil.which(spec.command)
        if not resolved:
            raise RuntimeVerificationError(
                f"Spawn command {spec.command!r} not found on PATH."
            )
        argv = [resolved, *spec.argv]
        env = {key: os.environ.get(key, "") for key in spec.env_passthrough}
        env.setdefault("HOME", "/tmp")
        started_at = datetime.now(timezone.utc)
        start = time.monotonic()
        try:
            proc = subprocess.Popen(  # noqa: S603 — argv from positive contract
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=spec.cwd,
                bufsize=0,
                text=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeVerificationError(
                f"Could not spawn {spec.command!r}: {exc}"
            ) from exc

        stdout_buf = bytearray()
        stderr_buf = bytearray()
        deadline = start + spec.timeout_seconds
        truncated = False
        timed_out = False

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                pipes = [p for p in (proc.stdout, proc.stderr) if p is not None]
                if not pipes or proc.poll() is not None:
                    # Drain any remaining buffered bytes before exit.
                    for pipe, buf in (
                        (proc.stdout, stdout_buf),
                        (proc.stderr, stderr_buf),
                    ):
                        if pipe is None:
                            continue
                        try:
                            chunk = pipe.read()
                        except (OSError, ValueError):
                            chunk = b""
                        if chunk:
                            buf.extend(chunk)
                            if len(buf) > spec.max_bytes:
                                del buf[spec.max_bytes:]
                                truncated = True
                    break
                ready, _, _ = select.select(pipes, [], [], min(remaining, 0.5))
                for pipe in ready:
                    chunk = pipe.read1(4096) if hasattr(pipe, "read1") else pipe.read(4096)
                    if not chunk:
                        continue
                    target = stdout_buf if pipe is proc.stdout else stderr_buf
                    target.extend(chunk)
                    if len(target) > spec.max_bytes:
                        del target[spec.max_bytes:]
                        truncated = True
        finally:
            _terminate(proc)

        end = time.monotonic()
        completed_at = datetime.now(timezone.utc)
        exit_code = proc.returncode if proc.returncode is not None else -1
        return SandboxRunResult(
            command=spec.command,
            argv=tuple(spec.argv),
            exit_code=exit_code,
            stdout=bytes(stdout_buf).decode("utf-8", errors="replace"),
            stderr=bytes(stderr_buf).decode("utf-8", errors="replace"),
            duration_seconds=round(end - start, 4),
            started_at=started_at,
            completed_at=completed_at,
            timed_out=timed_out,
            truncated=truncated,
        )


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
    finally:
        for pipe in (proc.stdout, proc.stderr):
            if pipe and not pipe.closed:
                try:
                    pipe.close()
                except OSError:
                    pass


# -------- HEAD client for api_endpoint_exists --------


class HttpHeadClient(Protocol):
    def head(
        self, url: str, *, timeout: float, max_redirects: int, resolution
    ):
        ...


class HttpxHeadClient:
    def head(
        self, url: str, *, timeout: float, max_redirects: int, resolution
    ):
        # Redirects are not followed on purpose. The verifier treats 3xx as
        # "endpoint exists" proof, so we can pin the connect step to the
        # already-vetted IP and still avoid chasing a Location header into an
        # unvalidated host.
        return safe_https_request(
            "HEAD",
            resolution=resolution,
            headers={},
            timeout=timeout,
        )


# -------- Verifier outcome --------


@dataclass(frozen=True)
class VerifierOutcome:
    """Returned by each per-claim-type verifier.

    A verifier may decide the claim is `skipped` (this verifier knows the
    claim type isn't safely runtime-checkable). On a non-skip the verifier
    reports whether the check `passed`, an `artifact` to persist as audit
    evidence, and human-readable `reasons` to attach to the L3 / failed
    `VerificationResult`.
    """

    skipped: bool
    skip_reason: str = ""
    passed: bool = False
    artifact: RawIngestionArtifact | None = None
    reasons: list[str] = field(default_factory=list)
    verifier_kind: str = ""


class RuntimeClaimVerifier(Protocol):
    kind: str  # e.g. "cli_command_exists"

    def applies_to(self, claim: KnowledgeClaim) -> bool:
        ...

    def verify(self, claim: KnowledgeClaim) -> VerifierOutcome:
        ...


# -------- Concrete verifiers --------


_TOKEN_BOUNDARY = re.compile(r"\s+")
# A subcommand token is derived from documentation-sourced claim text and
# then handed to a spawned `<tool> <subcommand> --help`. There is no shell
# (argv is a list), but we still require a plain identifier shape so junk or
# shell-metacharacter tokens never reach the spawn as defence-in-depth.
_SAFE_SUBCOMMAND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class _ToolExistenceVerifier:
    """Verify `tool_exists` and `cli_command_exists` by spawning a tool-level
    existence check (`<tool> --version`) from the contract. We deliberately
    do NOT spawn arbitrary subcommands for `cli_command_exists` — the
    asserted command's name is captured-text from documentation we already
    trust at L2; the runtime check is `<tool>` is present + responds, which
    is what `--version` proves.
    """

    kind = "tool_existence"

    def __init__(self, sandbox: SandboxRunner) -> None:
        self._sandbox = sandbox

    def applies_to(self, claim: KnowledgeClaim) -> bool:
        return claim.claim_type in {ClaimType.TOOL_EXISTS, ClaimType.CLI_COMMAND_EXISTS}

    def verify(self, claim: KnowledgeClaim) -> VerifierOutcome:
        contract = _runtime_sources()
        table = contract.get("tool_existence_check") or {}
        entry = table.get(claim.tool_id)
        if entry is None:
            return VerifierOutcome(
                skipped=True,
                skip_reason=(
                    f"No tool-existence check configured for tool_id '{claim.tool_id}'."
                ),
                verifier_kind=self.kind,
            )
        spec = SandboxSpec(
            command=str(entry["command"]),
            argv=tuple(str(p) for p in entry["argv"]),
            timeout_seconds=int(contract["default_timeout_seconds"]),
            max_bytes=int(contract["default_max_bytes"]),
        )
        result = self._sandbox.run(spec)
        artifact = _artifact_from_sandbox_run(spec=spec, result=result)
        passed = result.exit_code == 0 and not result.timed_out
        reasons = [
            (
                f"Spawned `{spec.command} {' '.join(spec.argv)}` for tool "
                f"existence: exit_code={result.exit_code}, "
                f"duration_seconds={result.duration_seconds}, "
                f"timed_out={result.timed_out}."
            )[:_REASON_MAX_CHARS]
        ]
        if not passed:
            reasons.append(
                f"stderr (truncated): {result.stderr[:200].strip() or '(empty)'}"
            )
        return VerifierOutcome(
            skipped=False,
            passed=passed,
            artifact=artifact,
            reasons=reasons,
            verifier_kind=self.kind,
        )


class _CliFlagExistsVerifier:
    """Verify `cli_flag_exists` by spawning `<tool> --help` (or
    `<tool> help`, per contract) and grep'ing the captured help text for the
    asserted flag token. We never spawn the subcommand the flag belongs to."""

    kind = "cli_flag_help_grep"

    _FLAG_TOKEN = re.compile(r"--[A-Za-z][A-Za-z0-9_-]*")

    def __init__(self, sandbox: SandboxRunner) -> None:
        self._sandbox = sandbox

    def applies_to(self, claim: KnowledgeClaim) -> bool:
        return claim.claim_type == ClaimType.CLI_FLAG_EXISTS

    def verify(self, claim: KnowledgeClaim) -> VerifierOutcome:
        contract = _runtime_sources()
        table = contract.get("cli_command_help_check") or {}
        entry = table.get(claim.tool_id)
        if entry is None:
            return VerifierOutcome(
                skipped=True,
                skip_reason=(
                    f"No CLI help check configured for tool_id '{claim.tool_id}'."
                ),
                verifier_kind=self.kind,
            )
        # Try to parse the asserted flag + subcommand from the claim subject.
        # Expected shape: "<tool> <subcommand> --<flag>" (matches Stage 7a's
        # subject generator); we accept any subject containing a flag token.
        flag_match = self._FLAG_TOKEN.search(claim.subject) or self._FLAG_TOKEN.search(
            claim.statement
        )
        if not flag_match:
            return VerifierOutcome(
                skipped=True,
                skip_reason=(
                    "Could not extract a --flag token from claim subject / statement; "
                    "runtime grep is not possible."
                ),
                verifier_kind=self.kind,
            )
        flag = flag_match.group(0)
        # Subcommand: best-effort first non-flag token after the binary name.
        # The subject typically looks like "<binary> <subcommand> --<flag>"
        # (e.g. "gh status --short"). Skip flag tokens, the tool_id prefix
        # (e.g. "github" for `github-cli`), AND the contract-listed `command`
        # itself (e.g. "gh") so we don't pick the binary name as the
        # subcommand and end up running `<binary> <binary> --help`.
        binary = str(entry.get("command") or "").lower()
        tool_prefix = claim.tool_id.split("-")[0].lower()
        tokens = _TOKEN_BOUNDARY.split(claim.subject.strip())
        subcommand = ""
        for token in tokens:
            lower = token.lower()
            if token.startswith("-"):
                continue
            if lower == tool_prefix or lower == binary:
                continue
            subcommand = token
            break
        if not subcommand:
            return VerifierOutcome(
                skipped=True,
                skip_reason="Could not extract a subcommand from claim subject.",
                verifier_kind=self.kind,
            )
        if not _SAFE_SUBCOMMAND.match(subcommand):
            return VerifierOutcome(
                skipped=True,
                skip_reason=(
                    f"Refusing to spawn: subcommand token {subcommand!r} is not a "
                    "plain identifier."
                ),
                verifier_kind=self.kind,
            )
        argv_template = entry.get("argv_template") or []
        argv: list[str] = []
        for piece in argv_template:
            if piece == "{subcommand}":
                argv.append(subcommand)
            else:
                argv.append(str(piece))
        spec = SandboxSpec(
            command=str(entry["command"]),
            argv=tuple(argv),
            timeout_seconds=int(contract["default_timeout_seconds"]),
            max_bytes=int(contract["default_max_bytes"]),
        )
        result = self._sandbox.run(spec)
        artifact = _artifact_from_sandbox_run(spec=spec, result=result)
        # The flag must appear in either stdout or stderr (some tools print
        # help to stderr).
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        passed = result.exit_code in (0, 1) and (flag in combined) and not result.timed_out
        reasons = [
            (
                f"Spawned `{spec.command} {' '.join(spec.argv)}` and searched help "
                f"text for `{flag}`: found={flag in combined}, "
                f"exit_code={result.exit_code}, duration_seconds={result.duration_seconds}."
            )[:_REASON_MAX_CHARS]
        ]
        return VerifierOutcome(
            skipped=False,
            passed=passed,
            artifact=artifact,
            reasons=reasons,
            verifier_kind=self.kind,
        )


class _ApiEndpointExistsVerifier:
    """Verify `api_endpoint_exists` by issuing a single HTTP HEAD to the
    URL embedded in the claim's evidence `source_uri`. Goes through the
    shared SSRF guard using the tool's `official_hosts` as the allowlist."""

    kind = "api_endpoint_head"

    def __init__(self, client: HttpHeadClient) -> None:
        self._client = client

    def applies_to(self, claim: KnowledgeClaim) -> bool:
        return claim.claim_type == ClaimType.API_ENDPOINT_EXISTS

    def verify(self, claim: KnowledgeClaim) -> VerifierOutcome:
        contract = _runtime_sources()
        cfg = contract.get("api_endpoint_check") or {}
        if (cfg.get("method") or "").upper() != "HEAD":
            return VerifierOutcome(
                skipped=True,
                skip_reason="Runtime contract does not enable HEAD checks.",
                verifier_kind=self.kind,
            )
        # Pull the most recent evidence URL.
        evidence_url: str | None = None
        for ev in claim.evidence:
            parsed = urlparse(ev.source_uri)
            if parsed.scheme in ("http", "https"):
                # Drop fragment for HEAD.
                evidence_url = ev.source_uri.split("#", 1)[0]
                break
        if evidence_url is None:
            return VerifierOutcome(
                skipped=True,
                skip_reason="No http(s) evidence URL on claim to HEAD-check.",
                verifier_kind=self.kind,
            )

        allowed_hosts = _official_hosts_for_tool(claim.tool_id)
        try:
            resolution = resolve_url_for_safe_fetch(
                evidence_url, allowed_hosts=allowed_hosts
            )
        except HttpFetchError as exc:
            return VerifierOutcome(
                skipped=True,
                skip_reason=(
                    f"Refusing HEAD: SSRF guard rejected {evidence_url!r}: {exc}"
                ),
                verifier_kind=self.kind,
            )

        started_at = datetime.now(timezone.utc)
        start = time.monotonic()
        try:
            response = self._client.head(
                evidence_url,
                timeout=float(cfg.get("timeout_seconds", 5)),
                max_redirects=int(cfg.get("max_redirects", 3)),
                resolution=resolution,
            )
            status_code: int = response.status_code
            error: str | None = None
        except httpx.TimeoutException as exc:
            status_code = -1
            error = f"timeout: {exc}"
        except httpx.RequestError as exc:
            status_code = -1
            error = f"request_error: {exc}"
        end = time.monotonic()
        completed_at = datetime.now(timezone.utc)

        accepted = set(cfg.get("accepted_status_codes") or [])
        passed = status_code in accepted
        artifact = _artifact_from_http_head(
            url=evidence_url,
            method="HEAD",
            status_code=status_code,
            duration_seconds=round(end - start, 4),
            started_at=started_at,
            completed_at=completed_at,
            error=error,
        )
        reasons = [
            (
                f"HEAD {evidence_url} -> status={status_code} "
                f"({'accepted' if passed else 'not accepted'}) in "
                f"{round(end - start, 4)}s."
            )[:_REASON_MAX_CHARS]
        ]
        if error:
            reasons.append(f"HEAD error: {error[:_REASON_MAX_CHARS]}")
        return VerifierOutcome(
            skipped=False,
            passed=passed,
            artifact=artifact,
            reasons=reasons,
            verifier_kind=self.kind,
        )


class _McpToolExistsVerifier:
    """Verify `mcp_tool_exists` by re-spawning the originating MCP server
    via the Stage 7d runner, calling `tools/list`, and confirming the tool
    name still appears. We re-derive the `server_id` from the claim's
    `tool_id` (Stage 7d's tool_id == server_id by construction)."""

    kind = "mcp_tools_list_recheck"

    def __init__(self, runner: McpServerRunner) -> None:
        self._runner = runner

    def applies_to(self, claim: KnowledgeClaim) -> bool:
        return claim.claim_type == ClaimType.MCP_TOOL_EXISTS

    def verify(self, claim: KnowledgeClaim) -> VerifierOutcome:
        # Stage 7d sets subject = "<server_id>: <tool_name>" and tool_id = server_id.
        if ":" not in claim.subject:
            return VerifierOutcome(
                skipped=True,
                skip_reason="Cannot parse tool name from claim subject.",
                verifier_kind=self.kind,
            )
        _, _, tool_name = claim.subject.partition(":")
        tool_name = tool_name.strip()
        if not tool_name:
            return VerifierOutcome(
                skipped=True,
                skip_reason="Empty MCP tool name in claim subject.",
                verifier_kind=self.kind,
            )
        try:
            spec = resolve_mcp_server_spec(server_id=claim.tool_id)
        except McpIngestionError as exc:
            return VerifierOutcome(
                skipped=True,
                skip_reason=f"Cannot resolve MCP server spec: {exc}",
                verifier_kind=self.kind,
            )

        started_at = datetime.now(timezone.utc)
        start = time.monotonic()
        try:
            run_result = self._runner.run(spec)
            error: str | None = None
        except McpIngestionError as exc:
            run_result = None
            error = str(exc)
        end = time.monotonic()
        completed_at = datetime.now(timezone.utc)

        tools_seen: list[str] = []
        if run_result is not None and isinstance(run_result.tools_list_result, dict):
            for tool in run_result.tools_list_result.get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    tools_seen.append(tool["name"])
        passed = error is None and tool_name in tools_seen
        artifact = _artifact_from_mcp_recheck(
            server_id=spec.server_id,
            tool_name=tool_name,
            tools_seen=tools_seen,
            error=error,
            duration_seconds=round(end - start, 4),
            started_at=started_at,
            completed_at=completed_at,
        )
        reasons = [
            (
                f"Re-spawned MCP server `{spec.server_id}` and called tools/list: "
                f"tool `{tool_name}` present={tool_name in tools_seen}, "
                f"tools_returned={len(tools_seen)}, duration_seconds={round(end - start, 4)}."
            )[:_REASON_MAX_CHARS]
        ]
        if error:
            reasons.append(f"MCP runner error: {error[:_REASON_MAX_CHARS]}")
        return VerifierOutcome(
            skipped=False,
            passed=passed,
            artifact=artifact,
            reasons=reasons,
            verifier_kind=self.kind,
        )


class _SkipVerifier:
    """Catch-all for claim types whose verification would require
    triggering a side effect (`destructive_action`, `side_effect`, etc.)
    or which have no safe runtime check available."""

    kind = "not_runtime_verifiable"

    _NEVER_RUNTIME_CHECK: frozenset[str] = frozenset(
        {
            ClaimType.SIDE_EFFECT.value,
            ClaimType.DESTRUCTIVE_ACTION.value,
            ClaimType.AUTH_REQUIREMENT.value,
            ClaimType.FEATURE_DEPRECATED.value,
            ClaimType.WORKFLOW_STEP.value,
            ClaimType.CONFIG_FIELD_EXISTS.value,
            ClaimType.ENVIRONMENT_REQUIREMENT.value,
        }
    )

    def applies_to(self, claim: KnowledgeClaim) -> bool:
        return claim.claim_type.value in self._NEVER_RUNTIME_CHECK

    def verify(self, claim: KnowledgeClaim) -> VerifierOutcome:
        return VerifierOutcome(
            skipped=True,
            skip_reason=(
                f"Claim type {claim.claim_type.value!r} is not runtime-verifiable "
                "without triggering side effects; Stage 8 deliberately refuses."
            ),
            verifier_kind=self.kind,
        )


# -------- Service --------


class RuntimeVerificationService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        sandbox: SandboxRunner | None = None,
        head_client: HttpHeadClient | None = None,
        mcp_runner: McpServerRunner | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        sandbox = sandbox or SubprocessSandboxRunner()
        head_client = head_client or HttpxHeadClient()
        mcp_runner = mcp_runner or SafeMcpServerRunner()
        self._verifiers: list[RuntimeClaimVerifier] = [
            _ToolExistenceVerifier(sandbox),
            _CliFlagExistsVerifier(sandbox),
            _ApiEndpointExistsVerifier(head_client),
            _McpToolExistsVerifier(mcp_runner),
            _SkipVerifier(),
        ]
        self._now = now

    def verify(
        self,
        *,
        claim_id: str,
        submitted_by: str = "runtime-verifier-agent",
    ) -> RuntimeVerificationResponse:
        claim = self._store.get(claim_id)
        if claim is None:
            raise RuntimeVerificationError(f"Claim '{claim_id}' does not exist.")

        # Find the most recent persisted verification result so we know the
        # current level. We refuse to promote unless the claim has already
        # reached L2_SOURCE_VERIFIED (per the verification_promotion_rules).
        prior = self._store.get_latest_verification_result(claim_id)
        if prior is None or prior.verification_level.value not in (
            VerificationLevel.L2_SOURCE_VERIFIED.value,
            VerificationLevel.L3_RUNTIME_VERIFIED.value,
            VerificationLevel.L4_CROSS_AGENT_VERIFIED.value,
        ):
            return RuntimeVerificationResponse(
                claim_id=claim_id,
                verifier_kind="precheck",
                runtime_check_passed=False,
                promoted_to_l3=False,
                new_verification_level=(
                    prior.verification_level if prior else VerificationLevel.L0_UNVERIFIED
                ),
                reasons=[
                    "Runtime verification requires the claim to be at "
                    "L2_source_verified or above; precheck refused."
                ],
                skipped=True,
                skip_reason=(
                    f"current_level="
                    f"{prior.verification_level.value if prior else 'none'}"
                ),
            )

        verifier = next(v for v in self._verifiers if v.applies_to(claim))
        outcome = verifier.verify(claim)

        if outcome.skipped:
            return RuntimeVerificationResponse(
                claim_id=claim_id,
                verifier_kind=outcome.verifier_kind,
                runtime_check_passed=False,
                promoted_to_l3=False,
                new_verification_level=prior.verification_level,
                reasons=outcome.reasons,
                skipped=True,
                skip_reason=outcome.skip_reason,
            )

        artifact_id = ""
        if outcome.artifact is not None:
            self._store.save_ingestion_artifact(outcome.artifact)
            artifact_id = outcome.artifact.artifact_id

        new_level = (
            VerificationLevel.L3_RUNTIME_VERIFIED
            if outcome.passed
            else prior.verification_level
        )
        verification = _build_runtime_verification_result(
            claim=claim,
            prior=prior,
            outcome=outcome,
            verified_at=self._timestamp(),
            new_level=new_level,
        )
        saved = self._store.save_verification_result(verification)

        return RuntimeVerificationResponse(
            claim_id=claim_id,
            verifier_kind=outcome.verifier_kind,
            runtime_check_passed=outcome.passed,
            promoted_to_l3=outcome.passed
            and new_level == VerificationLevel.L3_RUNTIME_VERIFIED,
            new_verification_level=new_level,
            captured_artifact_id=artifact_id,
            verification_id=saved.verification_id,
            reasons=outcome.reasons + ["submitted_by=" + submitted_by],
        )

    def verify_all_for_tool(
        self,
        *,
        tool_id: str,
        submitted_by: str = "runtime-verifier-agent",
        limit: int = 100,
    ) -> list[RuntimeVerificationResponse]:
        claims = self._store.list(tool_id=tool_id, limit=limit, offset=0)
        out: list[RuntimeVerificationResponse] = []
        for claim in claims:
            out.append(
                self.verify(claim_id=claim.claim_id, submitted_by=submitted_by)
            )
        return out

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)


# -------- Contract loading + helpers --------


@cache
def _runtime_sources() -> dict[str, Any]:
    with _RUNTIME_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_runtime_sources(data)
    return data


def _validate_runtime_sources(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("Runtime verification contract must have version=1")
    required = (
        "default_timeout_seconds",
        "default_max_bytes",
        "allowed_commands",
        "supported_claim_types",
        "tool_existence_check",
        "cli_command_help_check",
        "api_endpoint_check",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"Runtime verification contract missing {key}")
    for key in ("default_timeout_seconds", "default_max_bytes"):
        if not isinstance(data[key], int) or data[key] <= 0:
            raise ValueError(f"{key} must be a positive int")
    if not isinstance(data["allowed_commands"], list) or not data["allowed_commands"]:
        raise ValueError("allowed_commands must be a non-empty list")
    if not isinstance(data["supported_claim_types"], list) or not data["supported_claim_types"]:
        raise ValueError("supported_claim_types must be a non-empty list")
    allowed = set(data["allowed_commands"])
    for table_key in ("tool_existence_check", "cli_command_help_check"):
        table = data[table_key]
        if not isinstance(table, dict):
            raise ValueError(f"{table_key} must be a mapping")
        for tool_id, entry in table.items():
            if not isinstance(entry, dict) or "command" not in entry:
                raise ValueError(
                    f"{table_key}.{tool_id} must have a `command`."
                )
            if entry["command"] not in allowed:
                raise ValueError(
                    f"{table_key}.{tool_id} command {entry['command']!r} not in allowed_commands"
                )


def _allowed_commands() -> frozenset[str]:
    return frozenset(_runtime_sources()["allowed_commands"])


def _official_hosts_for_tool(tool_id: str) -> frozenset[str]:
    trust = _trust_sources()
    tool = trust["tools"].get(tool_id)
    if not tool:
        return frozenset()
    return frozenset(tool["official_hosts"])


# -------- Artifact factories --------


def _artifact_from_sandbox_run(
    *, spec: SandboxSpec, result: SandboxRunResult
) -> RawIngestionArtifact:
    payload = {
        "kind": "sandbox_subprocess",
        "command": spec.command,
        "argv": list(spec.argv),
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "timed_out": result.timed_out,
        "truncated": result.truncated,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    return _artifact_from_payload(payload)


def _artifact_from_http_head(
    *,
    url: str,
    method: str,
    status_code: int,
    duration_seconds: float,
    started_at: datetime,
    completed_at: datetime,
    error: str | None,
) -> RawIngestionArtifact:
    payload = {
        "kind": "http_head",
        "method": method,
        "url": url,
        "status_code": status_code,
        "duration_seconds": duration_seconds,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "error": error,
    }
    return _artifact_from_payload(payload)


def _artifact_from_mcp_recheck(
    *,
    server_id: str,
    tool_name: str,
    tools_seen: list[str],
    error: str | None,
    duration_seconds: float,
    started_at: datetime,
    completed_at: datetime,
) -> RawIngestionArtifact:
    payload = {
        "kind": "mcp_tools_list_recheck",
        "server_id": server_id,
        "asserted_tool_name": tool_name,
        "tools_seen": tools_seen,
        "tool_present": tool_name in tools_seen,
        "error": error,
        "duration_seconds": duration_seconds,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }
    return _artifact_from_payload(payload)


def _artifact_from_payload(payload: dict[str, Any]) -> RawIngestionArtifact:
    artifact_id = generate_ingestion_artifact_id()
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # Stage 8 verification artifacts are not tied to an ingestion run; we
    # synthesise a runtime run_id so the artifact row's NOT NULL run_id
    # constraint stays satisfied. The run_id prefix marks the lane.
    run_id = "rt_" + sha256(raw.encode("utf-8")).hexdigest()[:24]
    return RawIngestionArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=IngestionArtifactType.SANDBOX_EXECUTION_LOG,
        source_uri=f"ayiru://verification/runtime/{artifact_id}",
        raw_content=raw,
        hash=f"sha256:{sha256(raw.encode('utf-8')).hexdigest()}",
        captured_at=datetime.now(timezone.utc),
    )


def _runtime_evidence(
    *,
    claim: KnowledgeClaim,
    artifact: RawIngestionArtifact | None,
    passed: bool,
    excerpt: str,
) -> Evidence:
    source_uri = (
        artifact.source_uri
        if artifact is not None
        else f"ayiru://verification/runtime/{claim.claim_id}"
    )
    excerpt_text = (excerpt or ("runtime_pass" if passed else "runtime_fail"))[:8000]
    return Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=EvidenceType.SANDBOX_EXECUTION,
        source_uri=source_uri,
        excerpt=excerpt_text,
        hash=artifact.hash if artifact else f"sha256:{sha256(excerpt_text.encode()).hexdigest()}",
        captured_at=datetime.now(timezone.utc),
        trust_level=TrustLevel.HIGH,
    )


def _build_runtime_verification_result(
    *,
    claim: KnowledgeClaim,
    prior: VerificationResult,
    outcome: VerifierOutcome,
    verified_at: datetime,
    new_level: VerificationLevel,
) -> VerificationResult:
    runtime_evidence = _runtime_evidence(
        claim=claim,
        artifact=outcome.artifact,
        passed=outcome.passed,
        excerpt="; ".join(outcome.reasons)[:8000],
    )
    augmented_claim = claim.model_copy(
        update={"evidence": list(claim.evidence) + [runtime_evidence]}
    )
    base_breakdown = compute_confidence_breakdown(augmented_claim)
    adjustment = (
        _RUNTIME_PROMOTION_BONUS if outcome.passed else -_RUNTIME_FAIL_PENALTY
    )
    new_score = max(0.0, min(1.0, base_breakdown.score + adjustment))
    components = list(base_breakdown.components)
    components.append(
        ConfidenceComponent(
            source="runtime:" + outcome.verifier_kind,
            delta=round(adjustment, 4),
            reason=(
                "Runtime check passed; awarding +0.10."
                if outcome.passed
                else "Runtime check failed; penalising -0.20."
            ),
        )
    )
    breakdown = base_breakdown.model_copy(
        update={
            "score": round(new_score, 4),
            "band": band_for_score(round(new_score, 4)),
            "components": components,
            "penalties": (
                base_breakdown.penalties
                if outcome.passed
                else base_breakdown.penalties + ["runtime_check_failed"]
            ),
        }
    )

    if outcome.passed:
        decision = OrchestratorDecision.ACCEPTED
        status = VerificationStatus.ACCEPTED
    else:
        decision = OrchestratorDecision.REQUIRES_HUMAN_REVIEW
        status = VerificationStatus.REQUIRES_HUMAN_REVIEW

    # Risk doesn't change from a runtime check; re-classify from the claim
    # itself to keep the result self-contained (matches the orchestrator's
    # pattern of attaching a fresh RiskAssessment on every VerificationResult).
    risk_assessment: RiskAssessment = classify_action(
        augmented_claim.subject, augmented_claim.statement, augmented_claim.tool_id
    )

    return VerificationResult(
        verification_id=generate_verification_id(),
        claim_id=claim.claim_id,
        decision=decision,
        verification_status=status,
        verification_level=new_level,
        confidence=breakdown.score,
        confidence_band=ConfidenceBand(breakdown.band),
        confidence_breakdown=breakdown,
        risk_assessment=risk_assessment,
        reason_codes=(
            ["RUNTIME_VERIFIED"] if outcome.passed else ["RUNTIME_CHECK_FAILED"]
        ),
        reasons=outcome.reasons,
        verified_at=verified_at,
    )


__all__ = [
    "HttpHeadClient",
    "HttpxHeadClient",
    "RuntimeClaimVerifier",
    "RuntimeVerificationError",
    "RuntimeVerificationService",
    "SandboxRunResult",
    "SandboxRunner",
    "SandboxSpec",
    "SubprocessSandboxRunner",
    "VerifierOutcome",
]
