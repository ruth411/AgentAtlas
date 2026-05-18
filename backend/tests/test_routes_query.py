"""Stage 9 / Day 4: API integration tests for the /query/* surface.

Black-box tests that hit the live FastAPI app via TestClient. Each test
overrides `get_claim_store` to point at a fresh per-test SQLite DB so the
suite stays hermetic. Adversarial coverage:

- happy paths for all 5 endpoints
- structured 422s for bad input (regex-violating tool_id, oversized command,
  missing required field, extra forbidden field)
- structured 404 for unknown tool_spec
- query parameter clamping for search-tools (>500 rejected by Pydantic Query)
- default-deny verdict shape on the live API surface
- response models match the spec EXACTLY (no extra fields, no missing fields)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
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
from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown, ConfidenceComponent
from app.schemas.evidence import Evidence
from app.schemas.tool_spec import (
    AuthRequirement,
    Provenance,
    RiskProfile,
    ToolSpec,
)
from app.schemas.verification import VerificationResult
from app.schemas.workflow_spec import WorkflowSpec, WorkflowStep
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_verification_id,
)
from tests.helpers import risk_assessment, valid_sha256


FIXED_TIME = datetime(2026, 5, 18, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")


@pytest.fixture
def http(store: ClaimStore):
    app.dependency_overrides[get_claim_store] = lambda: store
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


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
        submitted_by="api-test",
        evidence=[
            Evidence(
                evidence_id=generate_evidence_id(),
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://git-scm.com/docs/git-status",
                excerpt="trusted excerpt",
                hash=valid_sha256(subject),
                captured_at=FIXED_TIME,
                trust_level=TrustLevel.HIGH,
            )
        ],
        risk_level=risk,
        created_at=FIXED_TIME,
    )
    store.create(claim)
    breakdown = ConfidenceBreakdown(
        score=confidence,
        band=ConfidenceBand.STRONG if confidence >= 0.90 else ConfidenceBand.HIGH,
        components=[
            ConfidenceComponent(source="t", delta=confidence, reason="x")
        ],
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
            confidence_band=breakdown.band,
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment(risk),
            reason_codes=["TEST"],
            reasons=["forced for api test"],
            verified_at=FIXED_TIME,
        )
    )
    return claim


def _publish_tool_spec(store: ClaimStore, *, tool_id: str, name: str | None = None) -> None:
    spec = ToolSpec(
        tool_id=tool_id,
        name=name or tool_id.title(),
        interfaces=["cli"],
        capabilities=["read", "status"],
        commands=[],
        auth=AuthRequirement(required=False, methods=[]),
        risk_profile=RiskProfile(default_risk_level=RiskLevel.LOW),
        provenance=Provenance(
            source_claim_ids=["c1"],
            source_evidence_ids=["e1"],
            compiled_at=FIXED_TIME,
            compiled_by="t",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        ),
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
    )
    store.save_canonical_tool_spec(spec)


def _publish_workflow(
    store: ClaimStore,
    *,
    workflow_id: str,
    goal: str,
    risk: RiskLevel = RiskLevel.LOW,
) -> None:
    spec = WorkflowSpec(
        workflow_id=workflow_id,
        goal=goal,
        tool_ids=["git"],
        steps=[
            WorkflowStep(step_id="s1", action="do", risk_level=risk),
        ],
        provenance=Provenance(
            source_claim_ids=["c1"],
            source_evidence_ids=["e1"],
            compiled_at=FIXED_TIME,
            compiled_by="t",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        ),
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
    )
    store.save_canonical_workflow_spec(spec)


# ---------------------------------------------------------------------------
# POST /query/validate-command
# ---------------------------------------------------------------------------


def test_validate_command_happy_path_returns_typed_verdict(
    http, store: ClaimStore
) -> None:
    _accept_claim(store, subject="git status", confidence=0.92)
    r = http.post(
        "/query/validate-command",
        json={"tool_id": "git", "command": "git status"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["match_method"] == "exact"
    assert body["safe_to_auto_execute"] is True
    assert body["risk_level"] == "low"
    assert body["verification_level"] == "L3_runtime_verified"
    assert body["confidence"] == 0.92
    assert isinstance(body["reasons"], list) and body["reasons"]
    assert isinstance(body["evidence"], list)


def test_validate_command_no_match_returns_default_deny_verdict(
    http,
) -> None:
    r = http.post(
        "/query/validate-command",
        json={"tool_id": "git", "command": "git nonexistent"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["match_method"] == "none"
    assert body["matched_claim_id"] is None
    assert body["safe_to_auto_execute"] is False
    assert body["requires_human_confirmation"] is True
    # Critical: null sentinel — not the magic string "unknown" (Day 1 audit).
    assert body["risk_level"] is None


def test_validate_command_critical_claim_blocks_auto_execute(
    http, store: ClaimStore
) -> None:
    _accept_claim(
        store,
        subject="gh repo delete",
        statement="Deletes a GitHub repository.",
        tool_id="github-cli",
        claim_type=ClaimType.DESTRUCTIVE_ACTION,
        risk=RiskLevel.CRITICAL,
        confidence=0.95,
    )
    r = http.post(
        "/query/validate-command",
        json={
            "tool_id": "github-cli",
            "command": "gh repo delete my-org/repo --yes",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["safe_to_auto_execute"] is False
    assert body["risk_level"] == "critical"
    assert body["match_method"] == "prefix"


def test_validate_command_rejects_bad_tool_id_format(http) -> None:
    r = http.post(
        "/query/validate-command",
        json={"tool_id": "bad tool id with spaces", "command": "x"},
    )
    assert r.status_code == 422
    assert "error" in r.json()


def test_validate_command_rejects_oversized_command(http) -> None:
    r = http.post(
        "/query/validate-command",
        json={"tool_id": "git", "command": "x" * 1000},
    )
    assert r.status_code == 422


def test_validate_command_rejects_missing_field(http) -> None:
    r = http.post("/query/validate-command", json={"tool_id": "git"})
    assert r.status_code == 422


def test_validate_command_rejects_extra_field(http) -> None:
    r = http.post(
        "/query/validate-command",
        json={"tool_id": "git", "command": "git status", "extra": "no"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /query/tools/{tool_id}
# ---------------------------------------------------------------------------


def test_get_tool_spec_returns_published_spec(http, store: ClaimStore) -> None:
    _publish_tool_spec(store, tool_id="git", name="Git")
    r = http.get("/query/tools/git")
    assert r.status_code == 200
    body = r.json()
    assert body["tool_id"] == "git"
    assert body["name"] == "Git"


def test_get_tool_spec_returns_structured_404(http) -> None:
    r = http.get("/query/tools/not-a-real-tool")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "CANONICAL_SPEC_NOT_FOUND"
    assert r.json()["error"]["details"]["tool_id"] == "not-a-real-tool"


# ---------------------------------------------------------------------------
# GET /query/search-tools
# ---------------------------------------------------------------------------


def test_search_tools_returns_typed_matches(http, store: ClaimStore) -> None:
    _publish_tool_spec(store, tool_id="git", name="Git")
    _publish_tool_spec(store, tool_id="github-cli", name="GitHub CLI")
    r = http.get("/query/search-tools?q=git")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    # Exact match must come first.
    assert body["matches"][0]["tool_id"] == "git"
    assert "exact" in body["matches"][0]["match_reason"]


def test_search_tools_empty_query_returns_all_specs(
    http, store: ClaimStore
) -> None:
    for tid in ("a-tool", "b-tool", "c-tool"):
        _publish_tool_spec(store, tool_id=tid)
    r = http.get("/query/search-tools")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3


def test_search_tools_rejects_oversized_limit(http) -> None:
    r = http.get("/query/search-tools?limit=10000")
    assert r.status_code == 422  # Pydantic Query(le=500) rejects this


def test_search_tools_rejects_negative_offset(http) -> None:
    r = http.get("/query/search-tools?offset=-1")
    assert r.status_code == 422


def test_search_tools_pagination_works(http, store: ClaimStore) -> None:
    for tid in ("a", "b", "c", "d"):
        _publish_tool_spec(store, tool_id=tid)
    r1 = http.get("/query/search-tools?limit=2&offset=0")
    r2 = http.get("/query/search-tools?limit=2&offset=2")
    assert r1.status_code == r2.status_code == 200
    page1_ids = [m["tool_id"] for m in r1.json()["matches"]]
    page2_ids = [m["tool_id"] for m in r2.json()["matches"]]
    assert page1_ids == ["a", "b"]
    assert page2_ids == ["c", "d"]


# ---------------------------------------------------------------------------
# POST /query/explain-risk
# ---------------------------------------------------------------------------


def test_explain_risk_returns_dimensions(http) -> None:
    r = http.post(
        "/query/explain-risk",
        json={"tool_id": "git", "action": "git status"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["risk_level"] is not None
    assert "risk_dimensions" in body
    dims = body["risk_dimensions"]
    for key in (
        "destructive_action",
        "mutates_remote_state",
        "reversible",
        "requires_auth",
        "may_cost_money",
        "may_expose_secrets",
    ):
        assert key in dims
        assert isinstance(dims[key], bool)


def test_explain_risk_cites_claim_when_match_exists(
    http, store: ClaimStore
) -> None:
    claim = _accept_claim(
        store,
        subject="gh repo delete",
        tool_id="github-cli",
        claim_type=ClaimType.DESTRUCTIVE_ACTION,
        risk=RiskLevel.CRITICAL,
    )
    r = http.post(
        "/query/explain-risk",
        json={"tool_id": "github-cli", "action": "gh repo delete --yes"},
    )
    assert r.status_code == 200
    assert claim.claim_id in r.json()["citing_claim_ids"]


def test_explain_risk_rejects_empty_action(http) -> None:
    r = http.post(
        "/query/explain-risk", json={"tool_id": "git", "action": ""}
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /query/safe-workflow
# ---------------------------------------------------------------------------


def test_safe_workflow_returns_sorted_safest_first(
    http, store: ClaimStore
) -> None:
    _publish_workflow(
        store, workflow_id="risky", goal="deploy app", risk=RiskLevel.CRITICAL
    )
    _publish_workflow(
        store, workflow_id="safe", goal="deploy app preview", risk=RiskLevel.LOW
    )
    r = http.post(
        "/query/safe-workflow",
        json={"goal": "deploy app"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    # Safest first.
    assert body["matches"][0]["aggregate_risk_level"] == "low"
    assert body["matches"][1]["aggregate_risk_level"] == "critical"


def test_safe_workflow_echoes_environment(http, store: ClaimStore) -> None:
    _publish_workflow(store, workflow_id="w1", goal="deploy")
    r = http.post(
        "/query/safe-workflow",
        json={"goal": "deploy", "environment": "staging"},
    )
    assert r.status_code == 200
    assert r.json()["environment"] == "staging"


def test_safe_workflow_no_match_returns_empty(http) -> None:
    r = http.post(
        "/query/safe-workflow",
        json={"goal": "totally unrelated goal text"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["matches"] == []


def test_safe_workflow_rejects_missing_goal(http) -> None:
    r = http.post("/query/safe-workflow", json={})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Response-shape invariants
# ---------------------------------------------------------------------------


def test_validate_command_response_has_no_extra_fields(
    http, store: ClaimStore
) -> None:
    """Pydantic `extra="forbid"` is enforced at the response model level.
    If the engine ever started emitting an unexpected field, the response
    serialisation would fail before it reached the wire."""
    _accept_claim(store, subject="git status")
    r = http.post(
        "/query/validate-command",
        json={"tool_id": "git", "command": "git status"},
    )
    assert r.status_code == 200
    expected = {
        "tool_id",
        "command",
        "matched_claim_id",
        "match_method",
        "safe_to_auto_execute",
        "requires_human_confirmation",
        "risk_level",
        "verification_level",
        "confidence",
        "confidence_band",
        "reasons",
        "evidence",
        "verdict_generated_at",
    }
    assert set(r.json().keys()) == expected


# ---------------------------------------------------------------------------
# Day 4 audit regressions
# ---------------------------------------------------------------------------


def test_get_tool_spec_path_param_rejects_invalid_format(http) -> None:
    """Regression: an earlier version had no regex constraint on the path
    `tool_id`, so `GET /query/tools/bad%20id` returned 404 while the same
    bad value in a body returned 422. Both routes must reject the same
    malformed input the same way."""
    r = http.get("/query/tools/bad%20tool%20id")
    assert r.status_code == 422


def test_get_tool_spec_path_param_rejects_oversized_format(http) -> None:
    r = http.get("/query/tools/" + "x" * 130)
    assert r.status_code == 422


def test_validate_command_safe_implies_no_required_confirmation(
    http, store: ClaimStore
) -> None:
    """Consistency invariant: a verdict can never be `safe_to_auto_execute=True`
    AND `requires_human_confirmation=True`. The two booleans express the
    same decision; flipping one without the other is a contradiction."""
    _accept_claim(store, subject="git status", confidence=0.92)
    r = http.post(
        "/query/validate-command",
        json={"tool_id": "git", "command": "git status"},
    )
    body = r.json()
    if body["safe_to_auto_execute"] is True:
        assert body["requires_human_confirmation"] is False
    if body["requires_human_confirmation"] is False:
        assert body["safe_to_auto_execute"] is True


def test_search_tools_response_has_no_extra_fields(
    http, store: ClaimStore
) -> None:
    _publish_tool_spec(store, tool_id="git", name="Git")
    r = http.get("/query/search-tools?q=git")
    body = r.json()
    assert set(body.keys()) == {"query", "matches", "total", "limit", "offset"}
    if body["matches"]:
        assert set(body["matches"][0].keys()) == {
            "tool_id",
            "name",
            "interfaces",
            "capability_count",
            "verified_command_count",
            "highest_risk_level",
            "verification_level",
            "match_reason",
        }


def test_explain_risk_response_has_no_extra_fields(http) -> None:
    r = http.post(
        "/query/explain-risk", json={"tool_id": "git", "action": "git status"}
    )
    body = r.json()
    assert set(body.keys()) == {
        "tool_id",
        "action",
        "risk_level",
        "risk_dimensions",
        "reasons",
        "citing_claim_ids",
        "confidence",
        "verdict_generated_at",
    }


def test_safe_workflow_response_has_no_extra_fields(
    http, store: ClaimStore
) -> None:
    _publish_workflow(store, workflow_id="w1", goal="deploy preview")
    r = http.post(
        "/query/safe-workflow",
        json={"goal": "deploy preview", "environment": "staging"},
    )
    body = r.json()
    assert set(body.keys()) == {"goal", "environment", "matches", "total"}
    if body["matches"]:
        assert set(body["matches"][0].keys()) == {
            "workflow_id",
            "goal",
            "tool_ids",
            "step_count",
            "aggregate_risk_level",
            "verification_level",
            "requires_confirmation",
        }


def test_route_layer_limits_match_query_policy_contract() -> None:
    """Drift lock: every numeric limit the route layer hardcodes (in
    `Query()` and `Field()`) MUST equal the contract value. FastAPI's
    Query/Field need static literals at module-import time, so the contract
    can't drive them dynamically — but if the contract changes, this test
    fails and forces the route literals to be updated in lockstep.

    Same pattern as `test_alembic_metadata_alignment.py`: catch drift at
    the test boundary instead of letting it ship silently."""
    import json
    from pathlib import Path as _Path

    contract = json.loads(
        (_Path(__file__).resolve().parents[2]
         / "contracts/query_policy.v1.json").read_text()
    )

    # Schema-level: ValidateCommandRequest.command max_length
    from app.schemas.query import ValidateCommandRequest

    command_field = ValidateCommandRequest.model_fields["command"]
    code_max_length = next(
        m.max_length for m in command_field.metadata if hasattr(m, "max_length")
    )
    assert code_max_length == contract["command_max_length"], (
        "ValidateCommandRequest.command max_length drifted from contract"
    )

    # Route-level: search_tools Query() parameters via signature inspection
    import inspect

    from app.api.routes_query import search_tools

    sig = inspect.signature(search_tools)
    q_default_metadata = sig.parameters["q"].default.metadata
    q_max_length = next(
        m.max_length for m in q_default_metadata if hasattr(m, "max_length")
    )
    assert q_max_length == contract["query_string_max_length"], (
        "search_tools `q` max_length drifted from contract"
    )

    limit_param = sig.parameters["limit"].default
    # limit has default + ge + le metadata
    le_value = next(
        m.le for m in limit_param.metadata if hasattr(m, "le")
    )
    assert le_value == contract["max_search_limit"], (
        "search_tools `limit` `le` drifted from contract max_search_limit"
    )
    assert limit_param.default == contract["default_search_limit"], (
        "search_tools `limit` default drifted from contract default_search_limit"
    )
