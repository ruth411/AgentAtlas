"""Stage 10: Ayiru MCP server adversarial tests.

These tests pin every wire-level contract of the server:

- Initialize handshake returns the right protocol version + server info
- `tools/list` enumerates every registered tool with its input schema
- `tools/call` dispatches correctly to all 6 tools and returns
  `structuredContent` + a JSON text block
- Tool execution errors surface as `isError: True` content blocks (NOT
  JSON-RPC errors) per the MCP spec
- Protocol errors (bad JSON, unknown method, malformed params) surface
  as JSON-RPC `error` responses
- Notifications get no reply
- A subprocess smoke test confirms `python -m app.mcp_server` runs
  cleanly and exits when stdin closes

Most tests use the in-process `McpServer.handle_frame` API to keep them
fast and hermetic. One subprocess test covers the actual `python -m` entry
point + stdio framing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.mcp_server import protocol as proto
from app.mcp_server.server import McpServer
from app.mcp_server.tools import list_tools
from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown, ConfidenceComponent
from app.schemas.enums import (
    ClaimType,
    ConfidenceBand,
    EvidenceType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.schemas.tool_spec import (
    AuthRequirement,
    Provenance,
    RiskProfile,
    ToolSpec,
)
from app.schemas.verification import VerificationResult
from app.schemas.workflow_spec import WorkflowSpec, WorkflowStep
from app.services.claim_store import ClaimStore
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_verification_id,
)
from tests.helpers import risk_assessment, valid_sha256


FIXED_TIME = datetime(2026, 5, 18, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


@pytest.fixture
def server(store: ClaimStore) -> McpServer:
    return McpServer(store)


def _frame(method: str, params: dict | None = None, request_id: int = 1) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
    )


def _notification(method: str, params: dict | None = None) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params or {}}
    )


def _initialize_params(secret: str | None = None) -> dict:
    params: dict[str, str] = {}
    if secret is not None:
        params["ayiru_shared_secret"] = secret
    return params


def _call_tool(
    server: McpServer, tool: str, arguments: dict, request_id: int = 1
) -> dict:
    raw = server.handle_frame(
        _frame(
            "tools/call",
            params={"name": tool, "arguments": arguments},
            request_id=request_id,
        )
    )
    assert raw is not None
    return json.loads(raw)


def _accept_claim(
    store: ClaimStore,
    *,
    subject: str,
    statement: str = "",
    tool_id: str = "git",
    claim_type: ClaimType = ClaimType.CLI_COMMAND_EXISTS,
    risk: RiskLevel = RiskLevel.LOW,
    level: VerificationLevel = VerificationLevel.L3_RUNTIME_VERIFIED,
    confidence: float = 0.92,
) -> KnowledgeClaim:
    claim = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=claim_type,
        subject=subject,
        statement=statement or subject,
        tool_id=tool_id,
        submitted_by="mcp-test",
        evidence=[
            Evidence(
                evidence_id=generate_evidence_id(),
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://git-scm.com/docs/git-status",
                excerpt="x",
                hash=valid_sha256(subject),
                captured_at=FIXED_TIME,
                trust_level=TrustLevel.HIGH,
            )
        ],
        risk_level=risk,
        created_at=FIXED_TIME,
    )
    store.create(claim)
    bd = ConfidenceBreakdown(
        score=confidence,
        band=ConfidenceBand.STRONG if confidence >= 0.90 else ConfidenceBand.HIGH,
        components=[ConfidenceComponent(source="t", delta=confidence, reason="x")],
        caps_applied=[],
        penalties=[],
    )
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim.claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=level,
            confidence=confidence,
            confidence_band=bd.band,
            confidence_breakdown=bd,
            risk_assessment=risk_assessment(risk),
            reason_codes=["T"],
            reasons=["t"],
            verified_at=FIXED_TIME,
        )
    )
    return claim


def _publish_tool_spec(store: ClaimStore, *, tool_id: str = "git") -> None:
    store.save_canonical_tool_spec(
        ToolSpec(
            tool_id=tool_id,
            name=tool_id.title(),
            interfaces=["cli"],
            capabilities=["read"],
            commands=[],
            auth=AuthRequirement(required=False, methods=[]),
            risk_profile=RiskProfile(default_risk_level=RiskLevel.LOW),
            provenance=Provenance(
                source_claim_ids=["c"],
                source_evidence_ids=["e"],
                compiled_at=FIXED_TIME,
                compiled_by="t",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            ),
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        )
    )


def _publish_workflow(store: ClaimStore, *, workflow_id: str = "w1") -> None:
    store.save_canonical_workflow_spec(
        WorkflowSpec(
            workflow_id=workflow_id,
            goal="deploy preview",
            tool_ids=["git"],
            steps=[WorkflowStep(step_id="s1", action="x", risk_level=RiskLevel.LOW)],
            provenance=Provenance(
                source_claim_ids=["c"],
                source_evidence_ids=["e"],
                compiled_at=FIXED_TIME,
                compiled_by="t",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            ),
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        )
    )


# ---------------------------------------------------------------------------
# Handshake + protocol-level framing
# ---------------------------------------------------------------------------


def test_initialize_returns_protocol_version_and_server_info(
    server: McpServer,
) -> None:
    raw = server.handle_frame(_frame("initialize", params=_initialize_params()))
    response = json.loads(raw)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == proto.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == proto.SERVER_NAME
    assert result["serverInfo"]["version"] == proto.SERVER_VERSION
    assert "tools" in result["capabilities"]


def test_initialize_requires_shared_secret_when_configured(store: ClaimStore) -> None:
    server = McpServer(store, shared_secret="shared-secret")

    raw = server.handle_frame(_frame("initialize", params=_initialize_params()))
    response = json.loads(raw)

    assert response["error"]["code"] == proto.AUTHENTICATION_REQUIRED
    assert "ayiru_shared_secret" in response["error"]["message"]


def test_initialize_rejects_wrong_shared_secret(store: ClaimStore) -> None:
    server = McpServer(store, shared_secret="shared-secret")

    raw = server.handle_frame(
        _frame("initialize", params=_initialize_params("wrong-secret"))
    )
    response = json.loads(raw)

    assert response["error"]["code"] == proto.AUTHENTICATION_REQUIRED


def test_initialize_accepts_correct_shared_secret_and_unlocks_session(
    store: ClaimStore,
) -> None:
    server = McpServer(store, shared_secret="shared-secret")

    init_raw = server.handle_frame(
        _frame("initialize", params=_initialize_params("shared-secret"))
    )
    init = json.loads(init_raw)
    assert init["result"]["protocolVersion"] == proto.PROTOCOL_VERSION

    tools_raw = server.handle_frame(_frame("tools/list", request_id=2))
    tools = json.loads(tools_raw)
    assert "tools" in tools["result"]


def test_methods_other_than_initialize_require_authenticated_session(
    store: ClaimStore,
) -> None:
    server = McpServer(store, shared_secret="shared-secret")

    raw = server.handle_frame(_frame("tools/list", request_id=7))
    response = json.loads(raw)

    assert response["error"]["code"] == proto.AUTHENTICATION_REQUIRED
    assert response["id"] == 7


def test_notifications_initialized_returns_no_reply(server: McpServer) -> None:
    assert server.handle_frame(_notification("notifications/initialized")) is None


def test_blank_line_returns_no_reply(server: McpServer) -> None:
    assert server.handle_frame("") is None
    assert server.handle_frame("\n") is None
    assert server.handle_frame("   \t  ") is None


def test_invalid_json_returns_parse_error(server: McpServer) -> None:
    raw = server.handle_frame("not json at all")
    response = json.loads(raw)
    assert response["error"]["code"] == proto.PARSE_ERROR


def test_missing_jsonrpc_version_returns_parse_error(server: McpServer) -> None:
    raw = server.handle_frame(json.dumps({"id": 1, "method": "initialize"}))
    response = json.loads(raw)
    assert response["error"]["code"] == proto.PARSE_ERROR


def test_unknown_method_returns_method_not_found(server: McpServer) -> None:
    raw = server.handle_frame(_frame("nonexistent/method"))
    response = json.loads(raw)
    assert response["error"]["code"] == proto.METHOD_NOT_FOUND


def test_resources_list_returns_empty(server: McpServer) -> None:
    raw = server.handle_frame(_frame("resources/list"))
    response = json.loads(raw)
    assert response["result"] == {"resources": []}


def test_prompts_list_returns_empty(server: McpServer) -> None:
    raw = server.handle_frame(_frame("prompts/list"))
    response = json.loads(raw)
    assert response["result"] == {"prompts": []}


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


def test_tools_list_returns_all_seven_tools(server: McpServer) -> None:
    """Stage 17 added `ask` as the 7th MCP tool; the v0.2 headline."""
    raw = server.handle_frame(_frame("tools/list"))
    response = json.loads(raw)
    names = {t["name"] for t in response["result"]["tools"]}
    assert names == {
        "ask",
        "validate_command",
        "get_tool_spec",
        "search_tools",
        "explain_risk",
        "get_safe_workflow",
        "submit_claim",
    }


def test_ask_is_the_first_tool_listed(server: McpServer) -> None:
    """LLM tool-choice is order-sensitive — the first listed tool gets
    the strongest implicit prior. Stage 17 puts `ask` first so an agent
    given Ayiru's MCP defaults reaches for ask() before web_search."""
    raw = server.handle_frame(_frame("tools/list"))
    response = json.loads(raw)
    tools = response["result"]["tools"]
    assert tools[0]["name"] == "ask"


def test_ask_description_teaches_family_hinting(server: McpServer) -> None:
    """The `ask` surface should teach the LLM that `tool_id_hint` accepts a
    coarse tool *family* name (e.g. 'ffmpeg') which Ayiru expands to the
    five-surface decomposition (commit bdd9ae3). Without this nudge the LLM
    leaves the hint empty or guesses an exact surface id and the matcher
    falls back unnecessarily."""
    raw = server.handle_frame(_frame("tools/list"))
    response = json.loads(raw)
    ask = next(t for t in response["result"]["tools"] if t["name"] == "ask")
    # The main description should nudge the model to set the hint.
    assert "tool_id_hint" in ask["description"]
    # The field description should explain family-name expansion + surfaces.
    hint = ask["inputSchema"]["properties"]["tool_id_hint"]["description"]
    assert "family" in hint.lower()
    assert "-recipes" in hint and "-errors" in hint
    assert "docker-cli" in hint  # exact-surface-id still works


def test_every_tool_has_required_metadata(server: McpServer) -> None:
    raw = server.handle_frame(_frame("tools/list"))
    response = json.loads(raw)
    for tool in response["result"]["tools"]:
        assert tool["name"]
        assert tool["description"]
        assert "inputSchema" in tool
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        # Every tool must explicitly forbid additionalProperties so a
        # client typo doesn't silently bypass validation.
        assert schema["additionalProperties"] is False


def test_tools_list_count_matches_registry() -> None:
    assert len(list_tools()) == 7


def test_every_tool_declares_mcp_2025_annotations(server: McpServer) -> None:
    """The 2026-05-22 dogfood session caught Claude Desktop hiding 3 of
    7 tools because we didn't declare MCP 2025-06-18 ``ToolAnnotations``.
    Without explicit ``readOnlyHint``, hosts fall back to name-prefix
    heuristics that auto-trust `get_*`/`search_*`/`explain_*` but gate
    `ask`/`validate_*`/`submit_*` — invisible filter, no error logged.

    This regression test pins the contract: every tool MUST declare
    annotations so the host treats them deterministically, not by
    name guesswork.
    """
    raw = server.handle_frame(_frame("tools/list"))
    response = json.loads(raw)
    for tool in response["result"]["tools"]:
        assert "annotations" in tool, (
            f"tool {tool['name']!r} is missing annotations — "
            "Claude Desktop will fall back to name-prefix heuristics. "
            "Add a ToolAnnotations dict with at least readOnlyHint."
        )
        annotations = tool["annotations"]
        assert "readOnlyHint" in annotations, (
            f"tool {tool['name']!r} annotations missing readOnlyHint"
        )
        assert "title" in annotations, (
            f"tool {tool['name']!r} annotations missing title (UX surface)"
        )


def test_read_tools_are_marked_read_only(server: McpServer) -> None:
    """The six query/lookup tools must declare ``readOnlyHint: true``.
    Only ``submit_claim`` writes state; if any read tool drifts to
    ``readOnlyHint: false`` it gets gated by the host UI and the LLM
    can no longer reach for it."""
    raw = server.handle_frame(_frame("tools/list"))
    response = json.loads(raw)
    read_only_expected = {
        "ask",
        "validate_command",
        "get_tool_spec",
        "search_tools",
        "explain_risk",
        "get_safe_workflow",
    }
    for tool in response["result"]["tools"]:
        if tool["name"] in read_only_expected:
            assert tool["annotations"]["readOnlyHint"] is True, (
                f"{tool['name']} must declare readOnlyHint=True or Claude Desktop "
                "will hide it from the LLM"
            )
    # submit_claim is the one write tool — explicitly NOT read-only.
    submit = next(t for t in response["result"]["tools"] if t["name"] == "submit_claim")
    assert submit["annotations"]["readOnlyHint"] is False


# ---------------------------------------------------------------------------
# tools/call validate_command
# ---------------------------------------------------------------------------


def test_validate_command_happy_path_returns_structured_verdict(
    server: McpServer, store: ClaimStore
) -> None:
    _accept_claim(store, subject="git status", confidence=0.92)
    response = _call_tool(
        server, "validate_command", {"tool_id": "git", "command": "git status"}
    )
    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["match_method"] == "exact"
    assert structured["safe_to_auto_execute"] is True
    assert structured["risk_level"] == "low"
    # Text block is the JSON-encoded structured content (for older clients).
    text_block = response["result"]["content"][0]
    assert text_block["type"] == "text"
    assert json.loads(text_block["text"]) == structured


def test_validate_command_no_match_returns_default_deny(
    server: McpServer,
) -> None:
    response = _call_tool(
        server, "validate_command",
        {"tool_id": "git", "command": "git nonexistent"},
    )
    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["match_method"] == "none"
    assert structured["safe_to_auto_execute"] is False
    # Critical: nullable risk_level, NOT a magic "unknown" sentinel.
    assert structured["risk_level"] is None


def test_validate_command_rejects_bad_tool_id_format(
    server: McpServer,
) -> None:
    response = _call_tool(
        server, "validate_command",
        {"tool_id": "bad tool with spaces", "command": "x"},
    )
    # Pydantic-level validation failures surface as tool-execution errors
    # (isError=True), not JSON-RPC errors — clients see "tool ran and
    # rejected the input" not "server is broken."
    assert response["result"]["isError"] is True


# ---------------------------------------------------------------------------
# tools/call get_tool_spec
# ---------------------------------------------------------------------------


def test_get_tool_spec_returns_published_spec(
    server: McpServer, store: ClaimStore
) -> None:
    _publish_tool_spec(store, tool_id="git")
    response = _call_tool(server, "get_tool_spec", {"tool_id": "git"})
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["tool_id"] == "git"


def test_get_tool_spec_returns_error_for_unknown_tool(
    server: McpServer,
) -> None:
    response = _call_tool(
        server, "get_tool_spec", {"tool_id": "not-a-tool"}
    )
    assert response["result"]["isError"] is True
    assert "No canonical" in response["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# tools/call search_tools
# ---------------------------------------------------------------------------


def test_search_tools_returns_matches(server: McpServer, store: ClaimStore) -> None:
    _publish_tool_spec(store, tool_id="git")
    response = _call_tool(server, "search_tools", {"query": "git"})
    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["total"] >= 1
    assert any(m["tool_id"] == "git" for m in structured["matches"])


def test_search_tools_empty_query_lists_all(
    server: McpServer, store: ClaimStore
) -> None:
    for tid in ("a-tool", "b-tool"):
        _publish_tool_spec(store, tool_id=tid)
    response = _call_tool(server, "search_tools", {"query": ""})
    assert response["result"]["structuredContent"]["total"] == 2


# ---------------------------------------------------------------------------
# tools/call explain_risk
# ---------------------------------------------------------------------------


def test_explain_risk_returns_dimensions(server: McpServer) -> None:
    response = _call_tool(
        server, "explain_risk",
        {"tool_id": "git", "action": "git status"},
    )
    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert "risk_dimensions" in structured
    for key in (
        "destructive_action",
        "mutates_remote_state",
        "reversible",
        "requires_auth",
        "may_cost_money",
        "may_expose_secrets",
    ):
        assert key in structured["risk_dimensions"]


# ---------------------------------------------------------------------------
# tools/call get_safe_workflow
# ---------------------------------------------------------------------------


def test_get_safe_workflow_returns_matches(
    server: McpServer, store: ClaimStore
) -> None:
    _publish_workflow(store, workflow_id="w1")
    response = _call_tool(
        server, "get_safe_workflow",
        {"goal": "deploy preview"},
    )
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["total"] == 1


# ---------------------------------------------------------------------------
# tools/call submit_claim (the write tool)
# ---------------------------------------------------------------------------


def test_submit_claim_persists_and_returns_verified_claim(
    server: McpServer, store: ClaimStore
) -> None:
    response = _call_tool(
        server, "submit_claim",
        {
            "claim_type": "cli_command_exists",
            "subject": "git status",
            "statement": "Shows the working tree status.",
            "tool_id": "git",
            "submitted_by": "mcp-test",
            "risk_level": "low",
            "evidence": [
                {
                    "evidence_type": "official_docs",
                    "source_uri": "https://git-scm.com/docs/git-status",
                    "excerpt": "Shows working tree status.",
                    "hash": "sha256:" + "a" * 64,
                    "captured_at": "2026-05-18T00:00:00+00:00",
                    "trust_level": "high",
                }
            ],
        },
    )
    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    # The claim was persisted and went through orchestration.
    assert structured["subject"] == "git status"
    assert structured["tool_id"] == "git"
    assert structured["verification_status"] in {
        "pending",
        "accepted",
        "requires_human_review",
    }
    # And the claim is queryable via the matcher (Bug A fix from Stage 9).
    cid = structured["claim_id"]
    assert store.get(cid) is not None


def test_submit_claim_rejects_unknown_tool_id_in_strict_mode(
    server: McpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 19: unknown tool_ids now persist at L0 by default. The
    v0.1 reject behavior is gated on AYIRU_STRICT_TOOL_LOCK=1. This
    test pins the strict-mode reject path through the MCP surface."""
    monkeypatch.setenv("AYIRU_STRICT_TOOL_LOCK", "1")
    response = _call_tool(
        server, "submit_claim",
        {
            "claim_type": "cli_command_exists",
            "subject": "x",
            "statement": "x",
            "tool_id": "not-a-real-tool",
            "submitted_by": "mcp-test",
            "risk_level": "low",
            "evidence": [
                {
                    "evidence_type": "official_docs",
                    "source_uri": "https://example.com",
                    "excerpt": "x",
                    "hash": "sha256:" + "a" * 64,
                    "captured_at": "2026-05-18T00:00:00+00:00",
                    "trust_level": "high",
                }
            ],
        },
    )
    assert response["result"]["isError"] is True
    assert "tool" in response["result"]["content"][0]["text"].lower()


def test_submit_claim_rejects_malformed_evidence(
    server: McpServer,
) -> None:
    response = _call_tool(
        server, "submit_claim",
        {
            "claim_type": "cli_command_exists",
            "subject": "x",
            "statement": "x",
            "tool_id": "git",
            "submitted_by": "mcp-test",
            "risk_level": "low",
            "evidence": [],  # empty — schema requires minItems=1
        },
    )
    assert response["result"]["isError"] is True


# ---------------------------------------------------------------------------
# tools/call protocol-level edges
# ---------------------------------------------------------------------------


def test_tools_call_with_unknown_tool_name_returns_jsonrpc_error(
    server: McpServer,
) -> None:
    raw = server.handle_frame(
        _frame(
            "tools/call",
            params={"name": "no_such_tool", "arguments": {}},
        )
    )
    response = json.loads(raw)
    # Unknown tools are a *protocol* error (the client asked for something
    # the server doesn't advertise) — not a tool-execution error.
    assert response["error"]["code"] == proto.TOOL_NOT_FOUND


def test_tools_call_with_missing_name_returns_invalid_params(
    server: McpServer,
) -> None:
    raw = server.handle_frame(
        _frame("tools/call", params={"arguments": {}})
    )
    response = json.loads(raw)
    assert response["error"]["code"] == proto.INVALID_PARAMS


def test_tools_call_with_non_object_arguments_returns_invalid_params(
    server: McpServer,
) -> None:
    raw = server.handle_frame(
        _frame(
            "tools/call",
            params={"name": "validate_command", "arguments": "not-an-object"},
        )
    )
    response = json.loads(raw)
    assert response["error"]["code"] == proto.INVALID_PARAMS


def test_tool_handler_exception_returns_iserror_content(
    server: McpServer,
) -> None:
    """Pydantic validation failures inside a tool handler bubble up as
    `ValidationError`. The server must wrap these as `isError=True`
    content blocks (per MCP spec) instead of letting them crash the
    JSON-RPC stream."""
    response = _call_tool(
        server, "validate_command",
        {"tool_id": "git"},  # missing 'command' — Pydantic will raise
    )
    assert response["result"]["isError"] is True


# ---------------------------------------------------------------------------
# Subprocess integration smoke
# ---------------------------------------------------------------------------


def test_notification_method_with_stray_id_silently_acked(
    server: McpServer,
) -> None:
    """Regression: a buggy client sending `notifications/initialized` (or
    any other `notifications/*` method) with a stray `id` field used to
    trip the dispatcher's request path and get back a METHOD_NOT_FOUND
    error. MCP defines these as notifications by method name regardless
    of whether an id is present; the server must silently ack them."""
    raw = server.handle_frame(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "notifications/initialized",
                "params": {},
            }
        )
    )
    assert raw is None


def test_tools_call_empty_list_arguments_rejected_as_invalid_params(
    server: McpServer,
) -> None:
    """Regression: `arguments: []` (a falsy non-dict) used to slip past
    `or {}` and silently become an empty dict, which then crashed the
    tool handler with a ValidationError. It must be rejected as
    INVALID_PARAMS at the dispatcher boundary instead."""
    raw = server.handle_frame(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "validate_command", "arguments": []},
            }
        )
    )
    response = json.loads(raw)
    assert response["error"]["code"] == proto.INVALID_PARAMS


def test_tools_call_arguments_missing_treated_as_empty_dict(
    server: McpServer,
) -> None:
    """Spec: when `arguments` is missing or null, the tool runs with an
    empty arguments dict. The handler's own Pydantic validation then
    decides whether that's acceptable for the specific tool."""
    raw = server.handle_frame(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "validate_command"},
            }
        )
    )
    response = json.loads(raw)
    # validate_command requires tool_id + command, so the tool handler
    # ValidationErrors. That's an isError content, not a protocol error.
    assert "error" not in response
    assert response["result"]["isError"] is True


def test_non_dict_tool_handler_payload_is_caught_as_iserror(
    server: McpServer,
) -> None:
    """Regression: every registered tool returns a dict by contract, but if
    a future tool regresses to returning a list / scalar / None, the MCP
    spec says `structuredContent` must be a JSON object. Without this
    guard, the bad payload would be JSON-encoded into the text block and
    placed directly into `structuredContent`, producing a response shape
    that older MCP clients can't parse and newer ones treat as an
    inconsistency between the two content fields. The server now rejects
    non-dict payloads with a clear `isError` message so the bug surfaces
    at the wire, not downstream in the client."""
    from app.mcp_server import tools as tools_mod
    from app.mcp_server.tools import McpTool

    broken = McpTool(
        name="_test_broken_tool",
        description="A tool that returns a list, in violation of the dict contract.",
        input_schema={"type": "object", "additionalProperties": False},
        handler=lambda args, store: [1, 2, 3],
    )
    tools_mod._TOOL_REGISTRY.append(broken)
    try:
        response = _call_tool(server, "_test_broken_tool", {})
        assert response["result"]["isError"] is True
        assert "non-object payload" in response["result"]["content"][0]["text"]
        # And structuredContent must NOT carry the bad payload.
        assert "structuredContent" not in response["result"] or isinstance(
            response["result"].get("structuredContent"), dict
        )
    finally:
        tools_mod._TOOL_REGISTRY.remove(broken)


def test_submit_claim_malformed_payload_surfaces_real_missing_fields(
    server: McpServer,
) -> None:
    """Regression: a payload missing every field used to be short-circuited
    by the evidence-minimum pre-check, returning the misleading 'requires
    at least one piece of evidence' error instead of the real 'missing
    required fields' error. The validator now runs first so the error
    message points at the actual problem."""
    response = _call_tool(
        server,
        "submit_claim",
        {"this": "is", "completely": "wrong"},
    )
    assert response["result"]["isError"] is True
    message = response["result"]["content"][0]["text"]
    # The real Pydantic error should mention multiple missing fields,
    # not just talk about evidence.
    assert "claim_type" in message or "subject" in message or "tool_id" in message


def test_subprocess_entry_point_runs_handshake_cleanly(tmp_path) -> None:
    """One end-to-end smoke that exercises the actual `python -m
    app.mcp_server` entry point + stdio framing the way Claude Desktop /
    Cursor will. Other tests cover everything else in-process.

    Uses AYIRU_DATABASE_URL to point at a throwaway SQLite file so the
    spawned process doesn't write into the dev DB."""
    db_path = tmp_path / "mcp_smoke.db"
    # Initialise the schema before spawning so the server doesn't have to
    # run alembic itself.
    from app.db.session import init_db, create_database_engine

    init_db(create_database_engine(f"sqlite:///{db_path}"))

    backend_dir = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_server"],
        cwd=backend_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", "/tmp"),
            "AYIRU_DATABASE_URL": f"sqlite:///{db_path}",
            # Force Python to find the `app` package by including the
            # backend dir on sys.path (cwd alone isn't enough on every OS).
            "PYTHONPATH": str(backend_dir),
        },
    )
    try:
        # Send initialize + initialized + tools/list; expect 2 reply lines
        # (initialize result + tools/list result; the notification gets no
        # reply).
        request_lines = [
            _frame("initialize", params=_initialize_params(), request_id=1),
            _notification("notifications/initialized"),
            _frame("tools/list", request_id=2),
        ]
        proc.stdin.write("\n".join(request_lines) + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        out_lines = []
        # Read until EOF (server exits when stdin closes).
        for line in proc.stdout:
            line = line.strip()
            if line:
                out_lines.append(line)

        proc.wait(timeout=5)
        assert proc.returncode == 0, f"server exited {proc.returncode}; stderr: {proc.stderr.read()}"
        assert len(out_lines) == 2
        init_response = json.loads(out_lines[0])
        assert init_response["result"]["protocolVersion"] == proto.PROTOCOL_VERSION
        tools_response = json.loads(out_lines[1])
        tool_names = {t["name"] for t in tools_response["result"]["tools"]}
        assert len(tool_names) == 7
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_subprocess_entry_point_accepts_shared_secret_when_configured(tmp_path) -> None:
    db_path = tmp_path / "mcp_smoke_secret.db"
    from app.db.session import init_db, create_database_engine

    init_db(create_database_engine(f"sqlite:///{db_path}"))

    backend_dir = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_server"],
        cwd=backend_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", "/tmp"),
            "AYIRU_DATABASE_URL": f"sqlite:///{db_path}",
            "AYIRU_MCP_SHARED_SECRET": "shared-secret",
            "PYTHONPATH": str(backend_dir),
        },
    )
    try:
        request_lines = [
            _frame(
                "initialize",
                params=_initialize_params("shared-secret"),
                request_id=1,
            ),
            _notification("notifications/initialized"),
            _frame("tools/list", request_id=2),
        ]
        proc.stdin.write("\n".join(request_lines) + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        out_lines = [line.strip() for line in proc.stdout if line.strip()]

        proc.wait(timeout=5)
        assert proc.returncode == 0, (
            f"server exited {proc.returncode}; stderr: {proc.stderr.read()}"
        )
        assert len(out_lines) == 2
        init = json.loads(out_lines[0])
        assert init["result"]["protocolVersion"] == proto.PROTOCOL_VERSION
        tools = json.loads(out_lines[1])
        assert "tools" in tools["result"]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
