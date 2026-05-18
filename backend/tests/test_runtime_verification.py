"""Stage 8: Runtime verification adversarial tests.

These tests pin every layer of the L2 → L3 promotion path: the
pre-check (refuse to promote claims still at L1), the per-claim-type
verifier registry, the SubprocessSandboxRunner allowlist + caps, HTTP HEAD
verification with SSRF guard, MCP re-spawn verification, the deliberate
skip-list for claim types whose verification would require side effects,
and the API surface (single + bulk + structured 422).

The production runner / production HTTP client / production MCP runner are
never invoked; tests use fakes so the suite stays hermetic and doesn't
depend on `git`, `npx`, or remote hosts being available.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes_verification import (
    get_head_client,
    get_runtime_mcp_runner,
    get_sandbox_runner,
)
from app.main import app
from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    IngestionArtifactType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.ids import generate_claim_id, generate_evidence_id
from app.services.mcp_ingestion import McpRunResult, McpServerSpec
from app.services.orchestrator import CanonOrchestrator
from app.services.runtime_verifier import (
    RuntimeVerificationError,
    RuntimeVerificationService,
    SandboxRunResult,
    SandboxSpec,
    SubprocessSandboxRunner,
)


FIXED_TIME = datetime(2026, 5, 17, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")


def _evidence(
    *,
    url: str = "https://git-scm.com/docs/git-status",
    etype: EvidenceType = EvidenceType.OFFICIAL_DOCS,
) -> Evidence:
    return Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=etype,
        source_uri=url,
        excerpt="Trusted source excerpt for runtime test.",
        hash="sha256:" + "a" * 64,
        captured_at=FIXED_TIME,
        trust_level=TrustLevel.HIGH,
    )


def _claim(
    *,
    claim_type: ClaimType,
    subject: str,
    statement: str,
    tool_id: str = "git",
    risk: RiskLevel = RiskLevel.LOW,
    evidence: list[Evidence] | None = None,
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=claim_type,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        submitted_by="runtime-test",
        evidence=evidence or [_evidence()],
        risk_level=risk,
        created_at=FIXED_TIME,
    )


def _persist_at_l2(store: ClaimStore, claim: KnowledgeClaim) -> None:
    """Persist the claim and stash an L2 VerificationResult.

    Prefers the natural orchestrator path (real `verify_claim`); falls back
    to a forced L2 result when the orchestrator's risk classifier disagrees
    with the claim's declared risk (which is fine for tests that only need
    the runtime verifier's precheck to pass)."""
    store.create(claim)
    result = CanonOrchestrator(store).verify_claim(claim)
    if result.verification_level != VerificationLevel.L2_SOURCE_VERIFIED:
        _save_forced_l2(store, claim)
    else:
        store.save_verification_result(result)


def _force_persist_at_l2(store: ClaimStore, claim: KnowledgeClaim) -> None:
    """Persist the claim and stash a synthetic L2 VerificationResult."""
    store.create(claim)
    _save_forced_l2(store, claim)


def _save_forced_l2(store: ClaimStore, claim: KnowledgeClaim) -> None:
    from app.schemas.confidence import ConfidenceBand as Band
    from app.schemas.verification import VerificationResult
    from app.services.confidence_scorer import compute_confidence_breakdown
    from app.services.ids import generate_verification_id
    from app.services.risk_classifier import classify_action

    breakdown = compute_confidence_breakdown(claim)
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim.claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=breakdown.score,
            confidence_band=Band(breakdown.band),
            confidence_breakdown=breakdown,
            risk_assessment=classify_action(
                claim.subject, claim.statement, claim.tool_id
            ),
            reason_codes=["TEST_FORCE_L2"],
            reasons=["Forced L2 for runtime verifier test."],
            verified_at=FIXED_TIME,
        )
    )


class FakeSandbox:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: str = "git version 2.42.0\n",
        stderr: str = "",
        timed_out: bool = False,
        raise_exc: Exception | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self._timed_out = timed_out
        self._raise = raise_exc
        self.calls: list[SandboxSpec] = []

    def run(self, spec: SandboxSpec) -> SandboxRunResult:
        self.calls.append(spec)
        if self._raise is not None:
            raise self._raise
        return SandboxRunResult(
            command=spec.command,
            argv=spec.argv,
            exit_code=self._exit_code,
            stdout=self._stdout,
            stderr=self._stderr,
            duration_seconds=0.01,
            started_at=FIXED_TIME,
            completed_at=FIXED_TIME,
            timed_out=self._timed_out,
        )


class FakeHeadClient:
    def __init__(
        self,
        *,
        status_code: int = 200,
        raise_exc: Exception | None = None,
    ) -> None:
        self._status_code = status_code
        self._raise = raise_exc
        self.calls: list[str] = []

    def head(self, url: str, *, timeout: float, max_redirects: int) -> httpx.Response:
        self.calls.append(url)
        if self._raise is not None:
            raise self._raise

        class _R:
            def __init__(self, sc: int) -> None:
                self.status_code = sc

        return _R(self._status_code)  # type: ignore[return-value]


class FakeMcpRunner:
    def __init__(
        self,
        *,
        tools: list[dict[str, Any]] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._tools = tools if tools is not None else [{"name": "read_file"}]
        self._raise = raise_exc

    def run(self, spec: McpServerSpec) -> McpRunResult:
        if self._raise is not None:
            raise self._raise
        return McpRunResult(
            raw_jsonrpc_log="",
            initialize_result={"protocolVersion": "2024-11-05"},
            tools_list_result={"tools": self._tools},
        )


# ---------------------------------------------------------------------------
# Pre-check (must be at L2 or above before runtime check fires)
# ---------------------------------------------------------------------------


def test_precheck_refuses_claim_at_l1(store: ClaimStore) -> None:
    # Create a claim but DON'T run the orchestrator: it stays at L0/L1 with
    # no persisted VerificationResult.
    claim = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="git status shows the working tree status.",
    )
    store.create(claim)

    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.skipped is True
    assert resp.promoted_to_l3 is False
    assert resp.runtime_check_passed is False
    assert "precheck" in resp.verifier_kind
    assert any("L2_source_verified" in r for r in resp.reasons)


def test_unknown_claim_raises(store: ClaimStore) -> None:
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    with pytest.raises(RuntimeVerificationError, match="does not exist"):
        svc.verify(claim_id="claim_does_not_exist")


# ---------------------------------------------------------------------------
# Tool-existence + CLI-command-exists verifier
# ---------------------------------------------------------------------------


def test_cli_command_exists_promotes_to_l3_on_exit_zero(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="git status shows the working tree status.",
    )
    _persist_at_l2(store, claim)

    sandbox = FakeSandbox(exit_code=0, stdout="git version 2.42.0")
    svc = RuntimeVerificationService(
        store, sandbox=sandbox, head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)

    assert resp.runtime_check_passed is True
    assert resp.promoted_to_l3 is True
    assert resp.new_verification_level == VerificationLevel.L3_RUNTIME_VERIFIED
    assert resp.verifier_kind == "tool_existence"
    assert resp.captured_artifact_id
    assert resp.verification_id

    # The sandbox was actually called with the contract-allowed argv.
    assert len(sandbox.calls) == 1
    spec = sandbox.calls[0]
    assert spec.command == "git"
    assert spec.argv == ("--version",)

    artifact = store.get_ingestion_artifact(resp.captured_artifact_id)
    assert artifact is not None
    assert artifact.artifact_type == IngestionArtifactType.SANDBOX_EXECUTION_LOG
    payload = json.loads(artifact.raw_content)
    assert payload["kind"] == "sandbox_subprocess"
    assert payload["command"] == "git"
    assert payload["exit_code"] == 0


def test_cli_command_exists_does_not_promote_on_nonzero_exit(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="git status shows the working tree status.",
    )
    _persist_at_l2(store, claim)

    sandbox = FakeSandbox(exit_code=127, stdout="", stderr="git: command not found")
    svc = RuntimeVerificationService(
        store, sandbox=sandbox, head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)

    assert resp.runtime_check_passed is False
    assert resp.promoted_to_l3 is False
    assert resp.new_verification_level == VerificationLevel.L2_SOURCE_VERIFIED
    # Artifact was still saved for audit.
    assert resp.captured_artifact_id


def test_cli_command_exists_does_not_promote_on_timeout(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="git status shows the working tree status.",
    )
    _persist_at_l2(store, claim)

    sandbox = FakeSandbox(exit_code=0, timed_out=True)
    svc = RuntimeVerificationService(
        store, sandbox=sandbox, head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is False
    assert resp.promoted_to_l3 is False


def test_tool_existence_skipped_for_unknown_tool(store: ClaimStore) -> None:
    # openai-api has no entry in `tool_existence_check`, so the verifier
    # should skip rather than spawn an unrelated binary.
    claim = _claim(
        claim_type=ClaimType.TOOL_EXISTS,
        subject="openai-api",
        statement="OpenAI API client tool is available.",
        tool_id="openai-api",
        risk=RiskLevel.LOW,
    )
    _force_persist_at_l2(store, claim)

    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.skipped is True
    assert "openai-api" in resp.skip_reason


# ---------------------------------------------------------------------------
# CLI flag exists verifier
# ---------------------------------------------------------------------------


def test_cli_flag_exists_passes_when_flag_appears_in_help(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.CLI_FLAG_EXISTS,
        subject="git status --short",
        statement="The --short flag prints a short status format.",
        tool_id="git",
    )
    _persist_at_l2(store, claim)

    sandbox = FakeSandbox(
        exit_code=0,
        stdout="usage: git status [<options>]\n  -s, --short  show status concisely\n",
    )
    svc = RuntimeVerificationService(
        store, sandbox=sandbox, head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is True
    assert resp.promoted_to_l3 is True
    # We spawned `git help status` per the contract.
    spec = sandbox.calls[0]
    assert spec.command == "git"
    assert "status" in spec.argv


def test_cli_flag_exists_fails_when_flag_missing_from_help(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.CLI_FLAG_EXISTS,
        subject="git status --bogus-flag",
        statement="The --bogus-flag flag does X.",
        tool_id="git",
    )
    _persist_at_l2(store, claim)

    sandbox = FakeSandbox(
        exit_code=0,
        stdout="usage: git status [<options>]\n  -s, --short  show status concisely\n",
    )
    svc = RuntimeVerificationService(
        store, sandbox=sandbox, head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is False
    assert resp.promoted_to_l3 is False


def test_cli_flag_exists_skips_binary_name_in_subject(store: ClaimStore) -> None:
    """Regression: subject `gh status --short` must resolve subcommand to
    `status`, not `gh` — otherwise we'd spawn `gh gh --help` and the runtime
    check would always fail. The fix skips both `tool_id`'s first dash-segment
    AND the contract `command` so the binary name never wins."""
    claim = _claim(
        claim_type=ClaimType.CLI_FLAG_EXISTS,
        subject="gh status --short",
        statement="The --short flag for gh status.",
        tool_id="github-cli",
    )
    _persist_at_l2(store, claim)

    sandbox = FakeSandbox(
        exit_code=0,
        stdout="usage: gh status [<options>]\n  -s, --short  short status\n",
    )
    svc = RuntimeVerificationService(
        store, sandbox=sandbox, head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is True
    # We must have spawned `gh status --help`, not `gh gh --help`.
    spec = sandbox.calls[0]
    assert "status" in spec.argv
    assert spec.argv.count("gh") == 0  # contract argv_template doesn't include "gh"


def test_cli_flag_exists_skips_when_no_flag_token_in_subject(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.CLI_FLAG_EXISTS,
        subject="git status",  # no --flag token at all
        statement="No flag mentioned anywhere here.",
        tool_id="git",
    )
    _persist_at_l2(store, claim)

    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.skipped is True
    assert "flag" in resp.skip_reason.lower()


# ---------------------------------------------------------------------------
# API endpoint exists (HEAD) verifier
# ---------------------------------------------------------------------------


def test_api_endpoint_exists_passes_on_accepted_status(
    store: ClaimStore, monkeypatch
) -> None:
    # Bypass DNS to avoid real network during SSRF resolution check.
    import socket as _socket

    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **k: [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    claim = _claim(
        claim_type=ClaimType.API_ENDPOINT_EXISTS,
        subject="POST /v1/chat/completions",
        statement="Create a chat completion.",
        tool_id="openai-api",
        evidence=[_evidence(url="https://api.openai.com/v1/chat/completions")],
    )
    _persist_at_l2(store, claim)

    head = FakeHeadClient(status_code=200)
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=head,
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is True
    assert resp.promoted_to_l3 is True
    assert head.calls == ["https://api.openai.com/v1/chat/completions"]
    assert resp.verifier_kind == "api_endpoint_head"


def test_api_endpoint_exists_passes_on_405_method_not_allowed(
    store: ClaimStore, monkeypatch
) -> None:
    """A HEAD against a POST-only endpoint typically returns 405; the
    contract treats 405 as 'endpoint exists' because the URL is valid."""
    import socket as _socket

    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **k: [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    claim = _claim(
        claim_type=ClaimType.API_ENDPOINT_EXISTS,
        subject="POST /v1/chat/completions",
        statement="Create a chat completion.",
        tool_id="openai-api",
        evidence=[_evidence(url="https://api.openai.com/v1/chat/completions")],
    )
    _persist_at_l2(store, claim)

    head = FakeHeadClient(status_code=405)
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=head,
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is True
    assert resp.promoted_to_l3 is True


def test_api_endpoint_exists_fails_on_500(store: ClaimStore, monkeypatch) -> None:
    import socket as _socket

    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **k: [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    claim = _claim(
        claim_type=ClaimType.API_ENDPOINT_EXISTS,
        subject="POST /v1/chat/completions",
        statement="Create a chat completion.",
        tool_id="openai-api",
        evidence=[_evidence(url="https://api.openai.com/v1/chat/completions")],
    )
    _persist_at_l2(store, claim)

    head = FakeHeadClient(status_code=500)
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=head,
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is False
    assert resp.promoted_to_l3 is False


def test_api_endpoint_exists_skips_when_evidence_url_not_in_allowlist(
    store: ClaimStore, monkeypatch
) -> None:
    import socket as _socket

    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **k: [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    claim = _claim(
        claim_type=ClaimType.API_ENDPOINT_EXISTS,
        subject="GET /thing",
        statement="A thing.",
        tool_id="openai-api",
        evidence=[_evidence(url="https://attacker.example.com/thing")],
    )
    _persist_at_l2(store, claim)

    head = FakeHeadClient(status_code=200)
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=head,
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.skipped is True
    assert "SSRF" in resp.skip_reason or "not in the allowed hosts" in resp.skip_reason
    # We must NOT have made any HEAD call.
    assert head.calls == []


def test_httpx_head_client_disables_redirects() -> None:
    """Regression guard for the SSRF-bypass bug: the production HEAD client
    must not follow redirects, because a redirect target wouldn't be
    re-checked against the per-tool host allowlist."""
    import inspect

    from app.services.runtime_verifier import HttpxHeadClient

    src = inspect.getsource(HttpxHeadClient.head)
    assert "follow_redirects=False" in src


def test_api_endpoint_exists_skips_when_no_http_evidence(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.API_ENDPOINT_EXISTS,
        subject="POST /v1/x",
        statement="x.",
        tool_id="openai-api",
        evidence=[_evidence(url="agentatlas://ingestion/run_1/artifacts/art_1")],
    )
    _persist_at_l2(store, claim)
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.skipped is True


# ---------------------------------------------------------------------------
# MCP tool exists verifier
# ---------------------------------------------------------------------------


def test_mcp_tool_exists_promotes_when_tool_still_listed(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.MCP_TOOL_EXISTS,
        subject="mcp-filesystem: read_file",
        statement="The filesystem server exposes read_file.",
        tool_id="mcp-filesystem",
    )
    _persist_at_l2(store, claim)

    runner = FakeMcpRunner(tools=[{"name": "read_file"}, {"name": "write_file"}])
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=runner, now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is True
    assert resp.promoted_to_l3 is True
    assert resp.verifier_kind == "mcp_tools_list_recheck"


def test_mcp_tool_exists_fails_when_tool_removed(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.MCP_TOOL_EXISTS,
        subject="mcp-filesystem: read_file",
        statement="The filesystem server exposes read_file.",
        tool_id="mcp-filesystem",
    )
    _persist_at_l2(store, claim)

    runner = FakeMcpRunner(tools=[{"name": "write_file_only"}])
    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=runner, now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.runtime_check_passed is False
    assert resp.promoted_to_l3 is False


def test_mcp_tool_exists_skips_when_subject_has_no_tool_name(
    store: ClaimStore,
) -> None:
    claim = _claim(
        claim_type=ClaimType.MCP_TOOL_EXISTS,
        subject="mcp-filesystem",  # no colon, no tool name
        statement="A tool exists.",
        tool_id="mcp-filesystem",
    )
    _persist_at_l2(store, claim)

    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.skipped is True


# ---------------------------------------------------------------------------
# Skip list for unsafe-to-runtime-check claim types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim_type,subject,statement,tool_id",
    [
        # Mutating mutation should never be auto-run.
        (
            ClaimType.DESTRUCTIVE_ACTION,
            "gh repo delete",
            "Permanently deletes a repository.",
            "github-cli",
        ),
        (
            ClaimType.SIDE_EFFECT,
            "POST /v1/chat/completions (side effect)",
            "Mutates state.",
            "openai-api",
        ),
        (
            ClaimType.AUTH_REQUIREMENT,
            "POST /v1/chat/completions (auth)",
            "Requires authentication.",
            "openai-api",
        ),
        (
            ClaimType.FEATURE_DEPRECATED,
            "git status --whatever (deprecated)",
            "Marked deprecated.",
            "git",
        ),
        (
            ClaimType.CONFIG_FIELD_EXISTS,
            "docker-compose.yml: services",
            "services field.",
            "docker",
        ),
    ],
)
def test_skipped_for_unsafe_claim_types(
    store: ClaimStore,
    claim_type: ClaimType,
    subject: str,
    statement: str,
    tool_id: str,
) -> None:
    claim = _claim(
        claim_type=claim_type,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        risk=(
            RiskLevel.CRITICAL
            if claim_type in {ClaimType.DESTRUCTIVE_ACTION, ClaimType.SIDE_EFFECT}
            else RiskLevel.LOW
        ),
    )
    store.create(claim)
    # Bring it manually to L2 by directly persisting a high-risk verification
    # if needed. For the skip path we only need a prior result at L2; the
    # orchestrator may stash these at L1 because of risk classification, so
    # we synthesize a passing L2 result directly.
    from app.services.confidence_scorer import compute_confidence_breakdown
    from app.services.risk_classifier import classify_action
    from app.services.ids import generate_verification_id
    from app.schemas.confidence import ConfidenceBand as Band
    from app.schemas.verification import VerificationResult

    breakdown = compute_confidence_breakdown(claim)
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim.claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=breakdown.score,
            confidence_band=Band(breakdown.band),
            confidence_breakdown=breakdown,
            risk_assessment=classify_action(claim.subject, claim.statement, claim.tool_id),
            reason_codes=["TEST_FORCE_L2"],
            reasons=["forced L2 for skip-list test"],
            verified_at=FIXED_TIME,
        )
    )

    svc = RuntimeVerificationService(
        store, sandbox=FakeSandbox(), head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    resp = svc.verify(claim_id=claim.claim_id)
    assert resp.skipped is True
    assert "side effect" in resp.skip_reason or "not runtime-verifiable" in resp.skip_reason


# ---------------------------------------------------------------------------
# Bulk per-tool
# ---------------------------------------------------------------------------


def test_bulk_verify_for_tool_iterates_all_claims(store: ClaimStore) -> None:
    a = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="Shows working tree status.",
    )
    b = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git log",
        statement="Shows commit log.",
    )
    _persist_at_l2(store, a)
    _persist_at_l2(store, b)

    sandbox = FakeSandbox(exit_code=0, stdout="git version 2.42.0")
    svc = RuntimeVerificationService(
        store, sandbox=sandbox, head_client=FakeHeadClient(),
        mcp_runner=FakeMcpRunner(), now=FIXED_TIME,
    )
    responses = svc.verify_all_for_tool(tool_id="git")
    assert len(responses) == 2
    assert all(r.promoted_to_l3 for r in responses)


# ---------------------------------------------------------------------------
# SubprocessSandboxRunner safety
# ---------------------------------------------------------------------------


def test_subprocess_runner_rejects_command_not_in_allowlist() -> None:
    bad = SandboxSpec(
        command="/usr/bin/curl",  # not in runtime_verification allowed_commands
        argv=("https://evil.example.com",),
        timeout_seconds=2,
        max_bytes=1024,
    )
    with pytest.raises(RuntimeVerificationError, match="not in allowed_commands"):
        SubprocessSandboxRunner().run(bad)


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_api_post_runtime_promotes_claim(store: ClaimStore) -> None:
    claim = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="git status shows the working tree status.",
    )
    _persist_at_l2(store, claim)

    sandbox = FakeSandbox(exit_code=0)
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_sandbox_runner] = lambda: sandbox
    app.dependency_overrides[get_head_client] = lambda: FakeHeadClient()
    app.dependency_overrides[get_runtime_mcp_runner] = lambda: FakeMcpRunner()
    try:
        with TestClient(app) as http:
            response = http.post(
                "/verification/runtime",
                json={"claim_id": claim.claim_id},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["runtime_check_passed"] is True
            assert body["promoted_to_l3"] is True
            assert body["new_verification_level"] == "L3_runtime_verified"
            assert body["captured_artifact_id"]
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_unknown_claim_with_structured_error(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_sandbox_runner] = lambda: FakeSandbox()
    app.dependency_overrides[get_head_client] = lambda: FakeHeadClient()
    app.dependency_overrides[get_runtime_mcp_runner] = lambda: FakeMcpRunner()
    try:
        with TestClient(app) as http:
            response = http.post(
                "/verification/runtime",
                json={"claim_id": "claim_does_not_exist"},
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "RUNTIME_VERIFICATION_FAILED"
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_reports_promoted_skipped_failed(store: ClaimStore) -> None:
    promoted = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="Shows working tree status.",
    )
    failed = _claim(
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git log",
        statement="Shows commit log.",
    )
    skipped = _claim(
        claim_type=ClaimType.FEATURE_DEPRECATED,
        subject="git status --whatever (deprecated)",
        statement="Marked deprecated.",
    )
    _persist_at_l2(store, promoted)
    _persist_at_l2(store, failed)
    # Force the deprecated claim to L2 for the skip test.
    from app.services.confidence_scorer import compute_confidence_breakdown
    from app.services.risk_classifier import classify_action
    from app.services.ids import generate_verification_id
    from app.schemas.confidence import ConfidenceBand as Band
    from app.schemas.verification import VerificationResult

    store.create(skipped)
    breakdown = compute_confidence_breakdown(skipped)
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=skipped.claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=breakdown.score,
            confidence_band=Band(breakdown.band),
            confidence_breakdown=breakdown,
            risk_assessment=classify_action(skipped.subject, skipped.statement, skipped.tool_id),
            reason_codes=["TEST_FORCE_L2"],
            reasons=["forced"],
            verified_at=FIXED_TIME,
        )
    )

    # Two sandboxes: one pass, one fail. Easiest: a runner that fails on its
    # second call to make `git log` fail while `git status` passes.
    class CountingSandbox:
        def __init__(self) -> None:
            self.n = 0

        def run(self, spec: SandboxSpec) -> SandboxRunResult:
            self.n += 1
            return SandboxRunResult(
                command=spec.command,
                argv=spec.argv,
                exit_code=0 if self.n == 1 else 127,
                stdout="git version 2.42.0" if self.n == 1 else "",
                stderr="" if self.n == 1 else "not found",
                duration_seconds=0.01,
                started_at=FIXED_TIME,
                completed_at=FIXED_TIME,
            )

    sandbox = CountingSandbox()
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_sandbox_runner] = lambda: sandbox
    app.dependency_overrides[get_head_client] = lambda: FakeHeadClient()
    app.dependency_overrides[get_runtime_mcp_runner] = lambda: FakeMcpRunner()
    try:
        with TestClient(app) as http:
            response = http.post("/verification/runtime/tools/git")
            assert response.status_code == 200
            body = response.json()
            assert body["attempted"] == 3
            assert body["promoted"] == 1
            assert body["skipped"] == 1
            assert body["failed"] == 1
    finally:
        app.dependency_overrides.clear()
