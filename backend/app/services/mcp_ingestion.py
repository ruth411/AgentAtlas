"""Stage 7d: Safe MCP server metadata ingestion.

This module spawns an allowlisted Model Context Protocol (MCP) server as a
local subprocess, communicates over stdio JSON-RPC to perform the standard
`initialize` handshake and a `tools/list` call, captures the raw JSON-RPC
reply as evidence, and emits one structured claim per MCP tool the server
advertises.

Per tool we emit:

- `mcp_tool_exists`     (primary)   — the server advertises a tool with this
                                       name + input schema.
- `side_effect`         (auxiliary) — the tool's `annotations.readOnlyHint`
                                       is missing or false (i.e. the tool may
                                       mutate state).
- `destructive_action`  (auxiliary) — `annotations.destructiveHint: true` OR
                                       the tool name matches a contract-listed
                                       destructive prefix (`delete`, `remove`,
                                       `destroy`, etc.).
- `feature_deprecated`  (auxiliary) — when the tool's description hints at
                                       deprecation (heuristic), or its
                                       annotations carry an explicit
                                       `deprecated: true`.

Provenance: the evidence `source_uri` is `ayiru://mcp/<server_id>/tools/
<tool_name>` (this is local-captured evidence, mirrored by the durable raw
`MCP_TOOL_LIST` artifact which stores the full JSON-RPC reply for audit).

The trust boundary: spawn commands are gated by a positive-shape argv
allowlist (each (command, argv-shape) pair must appear in
`contracts/mcp_ingestion_sources.v1.json`); only commands in
`allowed_commands` may be invoked; argv templates may use only the
placeholders listed in `allowed_template_placeholders`. The runner caps
total execution time and total bytes read so a misbehaving server cannot
exhaust resources, and treats every byte the server emits as untrusted data
(per the prompt-injection boundary).
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
from typing import Any, Protocol

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    IngestionArtifactType,
    IngestionStatus,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.schemas.ingestion import (
    IngestionRun,
    McpIngestionResponse,
    RawIngestionArtifact,
)
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
from app.services.contract_paths import contract_path
from app.services.orchestrator import CanonOrchestrator


_MCP_CONTRACT = contract_path("mcp_ingestion_sources.v1.json")

_STATEMENT_MAX_CHARS = 480
_SUBJECT_MAX_CHARS = 512


class McpIngestionError(ValueError):
    """Raised when an MCP ingestion request is unsupported, unsafe, or fails."""


# -------- Specs --------


@dataclass(frozen=True)
class McpServerSpec:
    server_id: str
    tool_id: str
    command: str
    argv: tuple[str, ...]
    publisher: str
    package_uri: str
    protocol_version: str
    timeout_seconds: int
    max_bytes: int
    max_tools_per_server: int
    destructive_tool_name_prefixes: tuple[str, ...]
    sandbox_dir: str | None
    database_url: str | None


@dataclass(frozen=True)
class McpRunResult:
    raw_jsonrpc_log: str  # newline-delimited JSON-RPC frames the server sent
    initialize_result: dict[str, Any]
    tools_list_result: dict[str, Any]


# -------- Runner protocol --------


class McpServerRunner(Protocol):
    """Spawn an MCP server and round-trip `initialize` + `tools/list`."""

    def run(self, spec: McpServerSpec) -> McpRunResult:
        ...


class SafeMcpServerRunner:
    """Production runner. Uses subprocess + stdio JSON-RPC with hard time and
    byte caps. Never inherits the parent's stdin. Always kills the process on
    cleanup so a misbehaving server cannot leak."""

    def run(self, spec: McpServerSpec) -> McpRunResult:
        if spec.command not in _allowed_commands():
            raise McpIngestionError(
                f"Spawn command {spec.command!r} is not in allowed_commands."
            )
        resolved = shutil.which(spec.command)
        if not resolved:
            raise McpIngestionError(
                f"Spawn command {spec.command!r} not found on PATH; install it first."
            )
        argv = [resolved, *spec.argv]
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            # Suppress npx update notices that pollute stdout/stderr.
            "NO_UPDATE_NOTIFIER": "1",
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        }
        try:
            # stderr -> DEVNULL on purpose. We do not read it, and well-behaved
            # MCP servers (npx / uvx) emit progress + warning chatter on stderr;
            # if we PIPE it without draining, the OS pipe buffer fills (~64 KiB
            # on Linux/macOS), the server blocks on its next stderr write, and
            # our select on stdout times out waiting for a response that the
            # server is forever blocked from sending. The result is a fake
            # "MCP server timed out" error and a leaked subprocess.
            proc = subprocess.Popen(  # noqa: S603 — argv comes from positive contract
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                bufsize=0,
                text=False,
            )
        except FileNotFoundError as exc:
            raise McpIngestionError(f"Could not spawn {spec.command!r}: {exc}") from exc

        try:
            return _drive_protocol(proc, spec)
        finally:
            _terminate(proc)


def _drive_protocol(
    proc: subprocess.Popen[bytes], spec: McpServerSpec
) -> McpRunResult:
    deadline = time.monotonic() + spec.timeout_seconds
    raw_buffer = bytearray()

    def write_frame(message: dict[str, Any]) -> None:
        data = (json.dumps(message) + "\n").encode("utf-8")
        if proc.stdin is None or proc.stdin.closed:
            raise McpIngestionError("MCP server stdin closed before request was sent.")
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise McpIngestionError(
                f"MCP server stdin broke during write: {exc}"
            ) from exc

    def read_reply(expected_id: int) -> dict[str, Any]:
        # Buffered line reader respecting deadline + max_bytes. Skips JSON-RPC
        # notifications (no `id`) and replies whose id we didn't ask for.
        line_buffer = bytearray()
        while True:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise McpIngestionError(
                    f"MCP server timed out after {spec.timeout_seconds}s before reply."
                )
            if proc.stdout is None:
                raise McpIngestionError("MCP server has no stdout pipe.")
            ready, _, _ = select.select([proc.stdout], [], [], remaining_time)
            if not ready:
                continue
            chunk = proc.stdout.read1(4096)
            if not chunk:
                # Server closed stdout.
                raise McpIngestionError(
                    "MCP server closed stdout before sending reply."
                )
            raw_buffer.extend(chunk)
            if len(raw_buffer) > spec.max_bytes:
                raise McpIngestionError(
                    f"MCP server exceeded max_bytes={spec.max_bytes} on stdout."
                )
            line_buffer.extend(chunk)
            while b"\n" in line_buffer:
                line, _, rest = line_buffer.partition(b"\n")
                line_buffer = bytearray(rest)
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    # Non-JSON noise on stdout — record but skip.
                    continue
                if not isinstance(parsed, dict):
                    continue
                if "id" not in parsed:
                    # Notification from server (`notifications/*`). Ignore.
                    continue
                if parsed.get("id") != expected_id:
                    continue
                if "error" in parsed:
                    raise McpIngestionError(
                        f"MCP server returned JSON-RPC error: {parsed['error']}"
                    )
                return parsed

    initialize_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": spec.protocol_version,
            "capabilities": {},
            "clientInfo": {
                "name": "Ayiru-McpIngestion",
                "version": "1.0",
            },
        },
    }
    write_frame(initialize_req)
    initialize_reply = read_reply(expected_id=1)
    initialize_result = initialize_reply.get("result") or {}
    if not isinstance(initialize_result, dict):
        raise McpIngestionError(
            "MCP server initialize result was not a JSON object."
        )

    # Required initialized notification (no reply).
    write_frame({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    })

    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    write_frame(list_req)
    list_reply = read_reply(expected_id=2)
    list_result = list_reply.get("result") or {}
    if not isinstance(list_result, dict):
        raise McpIngestionError(
            "MCP server tools/list result was not a JSON object."
        )

    return McpRunResult(
        raw_jsonrpc_log=bytes(raw_buffer).decode("utf-8", errors="replace"),
        initialize_result=initialize_result,
        tools_list_result=list_result,
    )


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
    finally:
        # stderr is DEVNULL by design — only stdin/stdout are pipes we own.
        for pipe in (proc.stdin, proc.stdout):
            if pipe and not pipe.closed:
                try:
                    pipe.close()
                except OSError:
                    pass


# -------- Service --------


class McpIngestionService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        runner: McpServerRunner | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        self._runner = runner or SafeMcpServerRunner()
        self._now = now

    def ingest(
        self,
        *,
        server_id: str,
        submitted_by: str = "mcp-ingestion-agent",
        verify: bool = True,
    ) -> McpIngestionResponse:
        spec = resolve_mcp_server_spec(server_id=server_id)
        run_id = generate_ingestion_run_id()
        started_at = self._timestamp()
        run = IngestionRun(
            run_id=run_id,
            tool_id=spec.tool_id,
            command=" ".join([spec.command, *spec.argv]),
            status=IngestionStatus.IN_PROGRESS,
            submitted_by=submitted_by,
            created_at=started_at,
            completed_at=None,
        )
        self._store.save_ingestion_run(run)

        errors: list[str] = []
        artifact_ids: list[str] = []
        created_claim_ids: list[str] = []
        verification_result_ids: list[str] = []
        tool_count = 0
        skipped_tools: list[str] = []
        protocol_version_negotiated = ""
        server_name = ""
        server_version = ""

        try:
            result = self._runner.run(spec)
            if not isinstance(result.initialize_result, dict):
                raise McpIngestionError(
                    "MCP server initialize result was not a JSON object."
                )
            if not isinstance(result.tools_list_result, dict):
                raise McpIngestionError(
                    "MCP server tools/list result was not a JSON object."
                )
            init_info = result.initialize_result.get("serverInfo") or {}
            if isinstance(init_info, dict):
                server_name = str(init_info.get("name") or "")[:256]
                server_version = str(init_info.get("version") or "")[:64]
            protocol_version_negotiated = str(
                result.initialize_result.get("protocolVersion") or ""
            )[:64]

            raw_tools = result.tools_list_result.get("tools")
            if not isinstance(raw_tools, list):
                raise McpIngestionError(
                    "MCP server tools/list result.tools must be a JSON array."
                )

            captured_at = self._timestamp()
            artifact = _artifact_from_result(
                run_id=run_id,
                spec=spec,
                result=result,
                captured_at=captured_at,
            )
            self._store.save_ingestion_artifact(artifact)
            artifact_ids.append(artifact.artifact_id)

            for index, tool in enumerate(raw_tools):
                if tool_count >= spec.max_tools_per_server:
                    skipped_tools.append(
                        str(tool.get("name") if isinstance(tool, dict) else f"#{index}")
                    )
                    continue
                if not isinstance(tool, dict):
                    skipped_tools.append(f"#{index} (not a JSON object)")
                    continue
                name = tool.get("name")
                if not isinstance(name, str) or not name:
                    skipped_tools.append(f"#{index} (missing name)")
                    continue
                tool_count += 1
                for claim in _claims_from_tool(
                    spec=spec,
                    artifact=artifact,
                    tool=tool,
                    submitted_by=submitted_by,
                    created_at=self._timestamp(),
                ):
                    try:
                        self._store.create(claim)
                    except DuplicateClaimError:
                        skipped_tools.append(claim.subject)
                        continue
                    created_claim_ids.append(claim.claim_id)
                    if verify:
                        verification = CanonOrchestrator(self._store).verify_claim(claim)
                        saved = self._store.save_verification_result(verification)
                        verification_result_ids.append(saved.verification_id)
        except (
            McpIngestionError,
            DuplicateClaimError,
            DuplicateEvidenceError,
            EvidencePolicyError,
            ToolNotAllowedError,
            ValueError,
        ) as exc:
            errors.append(str(exc))

        completed_at = self._timestamp()
        status = IngestionStatus.COMPLETED if not errors else IngestionStatus.FAILED
        self._store.save_ingestion_run(
            run.model_copy(
                update={
                    "status": status,
                    "completed_at": completed_at,
                    "summary": run.summary.model_copy(
                        update={
                            "capture_argv": [spec.command, *spec.argv],
                            "created_claim_ids": created_claim_ids,
                            "verification_result_ids": verification_result_ids,
                            "artifact_ids": artifact_ids,
                            "errors": errors,
                        }
                    ),
                }
            )
        )
        return McpIngestionResponse(
            run_id=run_id,
            status=status,
            server_id=spec.server_id,
            tool_id=spec.tool_id,
            publisher=spec.publisher,
            protocol_version_negotiated=protocol_version_negotiated,
            server_name=server_name,
            server_version=server_version,
            tool_count=tool_count,
            created_claim_ids=created_claim_ids,
            verification_result_ids=verification_result_ids,
            artifact_ids=artifact_ids,
            errors=errors,
            skipped_tools=skipped_tools,
        )

    def ingest_all_for_publisher(
        self,
        *,
        publisher: str,
        submitted_by: str = "mcp-ingestion-agent",
        verify: bool = True,
    ) -> list[McpIngestionResponse]:
        publisher = publisher.strip().lower()
        ids = [
            server_id
            for server_id, server in _mcp_sources()["servers"].items()
            if str(server.get("publisher", "")).strip().lower() == publisher
        ]
        if not ids:
            raise McpIngestionError(
                f"No MCP servers allowlisted for publisher '{publisher}'."
            )
        return [
            self.ingest(server_id=sid, submitted_by=submitted_by, verify=verify)
            for sid in ids
        ]

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)


# -------- Contract loading and lookup --------


def resolve_mcp_server_spec(
    *,
    server_id: str,
    sandbox_dir: str | None = None,
    database_url: str | None = None,
) -> McpServerSpec:
    data = _mcp_sources()
    normalized = server_id.strip()
    server = data["servers"].get(normalized)
    if server is None:
        raise McpIngestionError(
            f"MCP ingestion is not allowlisted for server '{server_id}'."
        )
    command = str(server["command"])
    if command not in _allowed_commands():
        raise McpIngestionError(
            f"server '{normalized}' uses disallowed spawn command {command!r}."
        )
    allowed_placeholders = set(data["allowed_template_placeholders"])
    rendered_argv: list[str] = []
    effective_sandbox = sandbox_dir or server.get("default_sandbox_dir")
    for piece in server["argv"]:
        if not isinstance(piece, str):
            raise McpIngestionError(
                f"server '{normalized}' has non-string argv element."
            )
        if piece.startswith("{") and piece.endswith("}"):
            if piece not in allowed_placeholders:
                raise McpIngestionError(
                    f"server '{normalized}' uses disallowed argv placeholder {piece!r}."
                )
            if piece == "{sandbox_dir}":
                if not effective_sandbox:
                    raise McpIngestionError(
                        f"server '{normalized}' requires sandbox_dir but none provided."
                    )
                rendered_argv.append(effective_sandbox)
            elif piece == "{database_url}":
                if not database_url:
                    raise McpIngestionError(
                        f"server '{normalized}' requires database_url but none provided."
                    )
                rendered_argv.append(database_url)
            else:  # pragma: no cover — guarded by allowed_template_placeholders
                raise McpIngestionError(
                    f"unhandled argv placeholder {piece!r}"
                )
        else:
            rendered_argv.append(piece)
    return McpServerSpec(
        server_id=normalized,
        tool_id=str(server["tool_id"]),
        command=command,
        argv=tuple(rendered_argv),
        publisher=str(server.get("publisher") or ""),
        package_uri=str(server.get("package_uri") or ""),
        protocol_version=str(data["protocol_version"]),
        timeout_seconds=int(data["default_timeout_seconds"]),
        max_bytes=int(data["default_max_bytes"]),
        max_tools_per_server=int(data["max_tools_per_server"]),
        destructive_tool_name_prefixes=tuple(
            str(p).lower() for p in data["destructive_tool_name_prefixes"]
        ),
        sandbox_dir=effective_sandbox,
        database_url=database_url,
    )


def list_mcp_server_ids() -> list[str]:
    return list(_mcp_sources()["servers"].keys())


@cache
def _mcp_sources() -> dict[str, Any]:
    with _MCP_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_mcp_sources(data)
    return data


def _allowed_commands() -> frozenset[str]:
    return frozenset(_mcp_sources()["allowed_commands"])


def _validate_mcp_sources(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("MCP ingestion source contract must have version=1")
    required = (
        "default_timeout_seconds",
        "default_max_bytes",
        "default_cache_ttl_seconds",
        "max_tools_per_server",
        "protocol_version",
        "destructive_tool_name_prefixes",
        "servers",
        "allowed_commands",
        "allowed_template_placeholders",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"MCP ingestion source contract missing {key}")
    for key in (
        "default_timeout_seconds",
        "default_max_bytes",
        "max_tools_per_server",
    ):
        if not isinstance(data[key], int) or data[key] <= 0:
            raise ValueError(f"{key} must be a positive int")
    allowed_commands = data["allowed_commands"]
    if not isinstance(allowed_commands, list) or not allowed_commands:
        raise ValueError("allowed_commands must be a non-empty list")
    allowed_placeholders = set(data["allowed_template_placeholders"])
    servers = data["servers"]
    if not isinstance(servers, dict) or not servers:
        raise ValueError("servers must be a non-empty mapping")

    # Trust contract must contain every MCP tool_id we ingest.
    from app.services.evidence_trust import _trust_sources

    trust_tools = set(_trust_sources()["tools"])
    for server_id, server in servers.items():
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("invalid MCP server id")
        for key in ("tool_id", "command", "argv", "publisher", "package_uri"):
            if key not in server:
                raise ValueError(f"server '{server_id}' missing {key}")
        if server["tool_id"] not in trust_tools:
            raise ValueError(
                f"MCP server '{server_id}' tool_id "
                f"{server['tool_id']!r} is not in tool_trust_sources contract"
            )
        if server["command"] not in allowed_commands:
            raise ValueError(
                f"server '{server_id}' command {server['command']!r} is not in allowed_commands"
            )
        if not isinstance(server["argv"], list):
            raise ValueError(f"server '{server_id}' argv must be a list")
        for piece in server["argv"]:
            if not isinstance(piece, str):
                raise ValueError(
                    f"server '{server_id}' argv contains non-string element"
                )
            if piece.startswith("{") and piece.endswith("}"):
                if piece not in allowed_placeholders:
                    raise ValueError(
                        f"server '{server_id}' uses disallowed placeholder {piece!r}"
                    )


# -------- Claim generation --------


def _is_destructive(tool: dict[str, Any], prefixes: tuple[str, ...]) -> bool:
    name = str(tool.get("name") or "").lower()
    if any(name.startswith(p) for p in prefixes):
        return True
    ann = tool.get("annotations")
    if isinstance(ann, dict) and ann.get("destructiveHint") is True:
        return True
    return False


def _is_read_only(tool: dict[str, Any]) -> bool:
    ann = tool.get("annotations")
    return isinstance(ann, dict) and ann.get("readOnlyHint") is True


def _is_deprecated(tool: dict[str, Any]) -> bool:
    ann = tool.get("annotations")
    if isinstance(ann, dict) and ann.get("deprecated") is True:
        return True
    description = str(tool.get("description") or "").lower()
    return "deprecated" in description.split()


def _claims_from_tool(
    *,
    spec: McpServerSpec,
    artifact: RawIngestionArtifact,
    tool: dict[str, Any],
    submitted_by: str,
    created_at: datetime,
) -> list[KnowledgeClaim]:
    name = str(tool["name"])
    subject_base = f"{spec.server_id}: {name}"[:_SUBJECT_MAX_CHARS]
    description = str(tool.get("description") or "")
    statement = (
        f"MCP server `{spec.server_id}` (publisher: {spec.publisher or 'unknown'}) "
        f"advertises tool `{name}`."
    )
    if description:
        statement = f"{statement} {' '.join(description.split())}"
    statement = statement[:_STATEMENT_MAX_CHARS]

    destructive = _is_destructive(tool, spec.destructive_tool_name_prefixes)
    read_only = _is_read_only(tool)
    deprecated = _is_deprecated(tool)

    if destructive:
        primary_risk = RiskLevel.CRITICAL
    elif read_only:
        primary_risk = RiskLevel.LOW
    else:
        primary_risk = RiskLevel.HIGH

    pointer = f"tools/{name}"
    claims: list[KnowledgeClaim] = [
        _build_claim(
            spec=spec,
            artifact=artifact,
            claim_type=ClaimType.MCP_TOOL_EXISTS,
            subject=subject_base,
            statement=statement,
            risk_level=primary_risk,
            pointer=pointer,
            submitted_by=submitted_by,
            created_at=created_at,
        )
    ]

    if not read_only:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                claim_type=ClaimType.SIDE_EFFECT,
                subject=f"{subject_base} (side effect)"[:_SUBJECT_MAX_CHARS],
                statement=(
                    f"MCP tool `{name}` on `{spec.server_id}` is not flagged "
                    f"read-only by its annotations."
                )[:_STATEMENT_MAX_CHARS],
                risk_level=RiskLevel.HIGH,
                pointer=pointer,
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    if destructive:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                claim_type=ClaimType.DESTRUCTIVE_ACTION,
                subject=f"{subject_base} (destructive)"[:_SUBJECT_MAX_CHARS],
                statement=(
                    f"MCP tool `{name}` on `{spec.server_id}` is destructive "
                    f"(annotation or name-prefix match)."
                )[:_STATEMENT_MAX_CHARS],
                risk_level=RiskLevel.CRITICAL,
                pointer=pointer,
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    if deprecated:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                claim_type=ClaimType.FEATURE_DEPRECATED,
                subject=f"{subject_base} (deprecated)"[:_SUBJECT_MAX_CHARS],
                statement=(
                    f"MCP tool `{name}` on `{spec.server_id}` appears deprecated."
                )[:_STATEMENT_MAX_CHARS],
                risk_level=RiskLevel.MEDIUM,
                pointer=f"{pointer}@deprecated",
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    return claims


def _build_claim(
    *,
    spec: McpServerSpec,
    artifact: RawIngestionArtifact,
    claim_type: ClaimType,
    subject: str,
    statement: str,
    risk_level: RiskLevel,
    pointer: str,
    submitted_by: str,
    created_at: datetime,
) -> KnowledgeClaim:
    # Evidence source URI is the atlas-captured artifact, with a fragment
    # naming the specific tool the claim describes.
    source_uri = f"ayiru://mcp/{spec.server_id}/{pointer}"
    excerpt = (statement or subject)[:8000]
    evidence = Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=EvidenceType.MCP_TOOL_SCHEMA,
        source_uri=source_uri,
        excerpt=excerpt,
        hash=artifact.hash,
        captured_at=artifact.captured_at,
        trust_level=TrustLevel.HIGH,
    )
    return KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=claim_type,
        subject=subject,
        statement=statement,
        tool_id=spec.tool_id,
        submitted_by=submitted_by,
        evidence=[evidence],
        risk_level=risk_level,
        created_at=created_at,
    )


def _artifact_from_result(
    *,
    run_id: str,
    spec: McpServerSpec,
    result: McpRunResult,
    captured_at: datetime,
) -> RawIngestionArtifact:
    artifact_id = generate_ingestion_artifact_id()
    source_uri = f"ayiru://ingestion/{run_id}/artifacts/{artifact_id}"
    payload = {
        "server_id": spec.server_id,
        "command": spec.command,
        "argv": list(spec.argv),
        "initialize_result": result.initialize_result,
        "tools_list_result": result.tools_list_result,
        "raw_jsonrpc_log": result.raw_jsonrpc_log,
    }
    raw_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return RawIngestionArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=IngestionArtifactType.MCP_TOOL_LIST,
        source_uri=source_uri,
        raw_content=raw_text,
        hash=f"sha256:{sha256(raw_text.encode('utf-8')).hexdigest()}",
        captured_at=captured_at,
    )


__all__ = [
    "McpIngestionError",
    "McpIngestionService",
    "McpRunResult",
    "McpServerRunner",
    "McpServerSpec",
    "SafeMcpServerRunner",
    "list_mcp_server_ids",
    "resolve_mcp_server_spec",
]
