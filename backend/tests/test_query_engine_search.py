"""Stage 9 / Day 3: QueryEngine adversarial tests for search_tools,
get_tool_spec, and find_safe_workflows.

These tests pin the search-tier scoring contract:

- get_tool_spec is a thin pass-through; returns None for unknown ids
- search_tools tiers: tool_id exact > tool_id substring > name substring >
  capability substring; ties broken by tool_id ASC for deterministic output
- search_tools respects limit + offset
- search_tools with empty query returns ALL specs in stable order
- find_safe_workflows sorts safest-first (aggregate_risk ASC)
- find_safe_workflows accepts but does not yet filter on `environment`
- find_safe_workflows scores by exact > substring > token overlap

Specs are hand-built (no compiler invocation) so the tests target the
engine's scoring + projection logic, not the spec compilation pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.enums import RiskLevel, VerificationLevel
from app.schemas.tool_spec import (
    AuthRequirement,
    CommandSpec,
    Provenance,
    RiskProfile,
    ToolSpec,
)
from app.schemas.workflow_spec import WorkflowSpec, WorkflowStep
from app.services.claim_store import ClaimStore
from app.services.query_engine import QueryEngine


FIXED_TIME = datetime(2026, 5, 18, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


@pytest.fixture
def engine(store: ClaimStore) -> QueryEngine:
    return QueryEngine(store, now=FIXED_TIME)


def _provenance(level: VerificationLevel) -> Provenance:
    return Provenance(
        source_claim_ids=["claim_one"],
        source_evidence_ids=["evidence_one"],
        compiled_at=FIXED_TIME,
        compiled_by="day3-test",
        verification_level=level,
    )


def _risk_profile(default: RiskLevel = RiskLevel.LOW) -> RiskProfile:
    return RiskProfile(default_risk_level=default)


def _command(
    name: str = "status", risk: RiskLevel = RiskLevel.LOW
) -> CommandSpec:
    return CommandSpec(
        name=name,
        summary=f"{name} summary",
        risk_level=risk,
        side_effects=[],
        requires_confirmation=risk
        in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL},
    )


def _tool_spec(
    *,
    tool_id: str,
    name: str | None = None,
    interfaces: list[str] | None = None,
    capabilities: list[str] | None = None,
    commands: list[CommandSpec] | None = None,
    level: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED,
    default_risk: RiskLevel = RiskLevel.LOW,
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        name=name or tool_id.title(),
        interfaces=interfaces or ["cli"],
        capabilities=capabilities or ["read"],
        commands=commands or [],
        auth=AuthRequirement(required=False, methods=[]),
        risk_profile=_risk_profile(default_risk),
        provenance=_provenance(level),
        verification_level=level,
    )


def _workflow_spec(
    *,
    workflow_id: str,
    goal: str,
    tool_ids: list[str] | None = None,
    steps: list[WorkflowStep] | None = None,
    level: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED,
) -> WorkflowSpec:
    default_step = WorkflowStep(
        step_id="step_1",
        action="verify",
        risk_level=RiskLevel.LOW,
    )
    return WorkflowSpec(
        workflow_id=workflow_id,
        goal=goal,
        tool_ids=tool_ids or ["git"],
        steps=steps or [default_step],
        provenance=_provenance(level),
        verification_level=level,
    )


# ---------------------------------------------------------------------------
# get_tool_spec
# ---------------------------------------------------------------------------


def test_get_tool_spec_returns_published_spec(
    engine: QueryEngine, store: ClaimStore
) -> None:
    spec = _tool_spec(tool_id="git", name="Git", capabilities=["status", "log"])
    store.save_canonical_tool_spec(spec)
    result = engine.get_tool_spec(tool_id="git")
    assert result is not None
    assert result.tool_id == "git"
    assert result.name == "Git"


def test_get_tool_spec_returns_none_for_unknown_tool(engine: QueryEngine) -> None:
    assert engine.get_tool_spec(tool_id="not-a-real-tool") is None


def test_get_tool_spec_strips_whitespace(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_tool_spec(_tool_spec(tool_id="git"))
    assert engine.get_tool_spec(tool_id="  git  ") is not None


# ---------------------------------------------------------------------------
# search_tools — tiered scoring
# ---------------------------------------------------------------------------


def test_search_tools_tool_id_exact_beats_substring(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_tool_spec(_tool_spec(tool_id="git", name="Git"))
    store.save_canonical_tool_spec(
        _tool_spec(tool_id="github-cli", name="GitHub CLI")
    )
    result = engine.search_tools(query="git")
    assert result.total >= 2
    # First match should be the exact `git`, not `github-cli`.
    assert result.matches[0].tool_id == "git"
    assert "exact" in result.matches[0].match_reason


def test_search_tools_substring_match_on_name(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_tool_spec(
        _tool_spec(tool_id="docker", name="Docker Engine")
    )
    result = engine.search_tools(query="engine")
    assert any(m.tool_id == "docker" for m in result.matches)
    docker = next(m for m in result.matches if m.tool_id == "docker")
    assert "name" in docker.match_reason


def test_search_tools_substring_match_on_capability(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_tool_spec(
        _tool_spec(
            tool_id="vercel-cli",
            name="Vercel CLI",
            capabilities=["deploy", "list_projects"],
        )
    )
    result = engine.search_tools(query="deploy")
    assert any(m.tool_id == "vercel-cli" for m in result.matches)
    vercel = next(m for m in result.matches if m.tool_id == "vercel-cli")
    assert "capability" in vercel.match_reason


def test_search_tools_no_match_excluded(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_tool_spec(
        _tool_spec(tool_id="git", name="Git", capabilities=["status"])
    )
    result = engine.search_tools(query="totally-unrelated-token")
    assert result.total == 0
    assert result.matches == []


# ---------------------------------------------------------------------------
# search_tools — empty query, pagination, ordering
# ---------------------------------------------------------------------------


def test_search_tools_empty_query_returns_all_specs(
    engine: QueryEngine, store: ClaimStore
) -> None:
    for tid in ("docker", "git", "vercel-cli"):
        store.save_canonical_tool_spec(_tool_spec(tool_id=tid))
    result = engine.search_tools(query="")
    assert result.total == 3
    # Empty-query ordering is deterministic by tool_id ASC.
    assert [m.tool_id for m in result.matches] == ["docker", "git", "vercel-cli"]


def test_search_tools_respects_limit_and_offset(
    engine: QueryEngine, store: ClaimStore
) -> None:
    for tid in ("a-tool", "b-tool", "c-tool", "d-tool"):
        store.save_canonical_tool_spec(_tool_spec(tool_id=tid))
    page1 = engine.search_tools(query="", limit=2, offset=0)
    assert page1.total == 4
    assert [m.tool_id for m in page1.matches] == ["a-tool", "b-tool"]
    page2 = engine.search_tools(query="", limit=2, offset=2)
    assert [m.tool_id for m in page2.matches] == ["c-tool", "d-tool"]


def test_search_tools_clamps_oversized_limit_to_contract_max(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_tool_spec(_tool_spec(tool_id="git"))
    result = engine.search_tools(query="", limit=10_000)
    # The contract caps at 500 — engine should silently clamp, not raise.
    assert result.limit == 500


# ---------------------------------------------------------------------------
# search_tools — summary projection
# ---------------------------------------------------------------------------


def test_search_tools_summary_reflects_command_count_and_risk(
    engine: QueryEngine, store: ClaimStore
) -> None:
    commands = [
        _command("status", risk=RiskLevel.LOW),
        _command("delete", risk=RiskLevel.CRITICAL),
    ]
    store.save_canonical_tool_spec(
        _tool_spec(
            tool_id="github-cli",
            name="GitHub CLI",
            commands=commands,
            level=VerificationLevel.L3_RUNTIME_VERIFIED,
            capabilities=["read", "write", "delete"],
        )
    )
    result = engine.search_tools(query="github-cli")
    match = result.matches[0]
    assert match.verified_command_count == 2
    assert match.highest_risk_level == RiskLevel.CRITICAL
    assert match.capability_count == 3
    assert match.verification_level == VerificationLevel.L3_RUNTIME_VERIFIED


def test_search_tools_summary_falls_back_to_risk_profile_for_no_commands(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_tool_spec(
        _tool_spec(
            tool_id="openai-api",
            commands=[],
            default_risk=RiskLevel.HIGH,
        )
    )
    result = engine.search_tools(query="openai-api")
    assert result.matches[0].highest_risk_level == RiskLevel.HIGH
    assert result.matches[0].verified_command_count == 0


# ---------------------------------------------------------------------------
# find_safe_workflows
# ---------------------------------------------------------------------------


def test_safe_workflows_returns_empty_for_empty_store(engine: QueryEngine) -> None:
    result = engine.find_safe_workflows(goal="deploy app")
    assert result.total == 0
    assert result.matches == []


def test_safe_workflows_exact_goal_match_beats_substring(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_workflow_spec(
        _workflow_spec(
            workflow_id="exact",
            goal="deploy to staging",
        )
    )
    store.save_canonical_workflow_spec(
        _workflow_spec(
            workflow_id="substring",
            goal="rollback after deploy to staging",
        )
    )
    result = engine.find_safe_workflows(goal="deploy to staging")
    assert result.total == 2
    # Both match; exact-goal-match scores higher. Same risk → exact wins on
    # the score tiebreak, even though ASC workflow_id would put 'exact' first
    # anyway. Lock the ordering rule.
    assert result.matches[0].workflow_id == "exact"


def test_safe_workflows_sorted_safest_first(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_workflow_spec(
        _workflow_spec(
            workflow_id="risky",
            goal="deploy production",
            steps=[
                WorkflowStep(
                    step_id="s1",
                    action="deploy",
                    risk_level=RiskLevel.CRITICAL,
                )
            ],
        )
    )
    store.save_canonical_workflow_spec(
        _workflow_spec(
            workflow_id="safe",
            goal="deploy preview",
            steps=[
                WorkflowStep(
                    step_id="s1",
                    action="preview",
                    risk_level=RiskLevel.LOW,
                )
            ],
        )
    )
    result = engine.find_safe_workflows(goal="deploy")
    assert result.total == 2
    # Safest first.
    assert result.matches[0].workflow_id == "safe"
    assert result.matches[0].aggregate_risk_level == RiskLevel.LOW
    assert result.matches[1].aggregate_risk_level == RiskLevel.CRITICAL


def test_safe_workflows_token_overlap_match(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Workflows with partial token overlap should still match (lower score
    than exact/substring but above zero)."""
    store.save_canonical_workflow_spec(
        _workflow_spec(
            workflow_id="overlap",
            goal="provision and deploy a new database cluster",
        )
    )
    result = engine.find_safe_workflows(goal="deploy database")
    assert any(m.workflow_id == "overlap" for m in result.matches)


def test_safe_workflows_environment_is_echoed_but_not_filtered(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """v1 doesn't filter by environment — workflow specs don't carry it yet.
    Confirm the field round-trips into the response so callers can rely on
    the API contract even before filtering becomes meaningful."""
    store.save_canonical_workflow_spec(
        _workflow_spec(workflow_id="w1", goal="deploy preview")
    )
    result = engine.find_safe_workflows(
        goal="deploy preview", environment="staging"
    )
    assert result.environment == "staging"
    assert result.total == 1


def test_safe_workflows_with_limit_greater_than_total_returns_all(
    engine: QueryEngine, store: ClaimStore
) -> None:
    store.save_canonical_workflow_spec(
        _workflow_spec(workflow_id="w1", goal="deploy preview")
    )
    result = engine.find_safe_workflows(goal="deploy preview", limit=100)
    assert result.total == 1
    assert len(result.matches) == 1


def test_safe_workflows_summary_aggregates_risk_and_confirmation(
    engine: QueryEngine, store: ClaimStore
) -> None:
    steps = [
        WorkflowStep(step_id="s1", action="check", risk_level=RiskLevel.LOW),
        WorkflowStep(
            step_id="s2",
            action="deploy",
            risk_level=RiskLevel.HIGH,
            requires_confirmation=True,
        ),
    ]
    store.save_canonical_workflow_spec(
        _workflow_spec(
            workflow_id="mixed",
            goal="run mixed-risk deploy",
            steps=steps,
        )
    )
    result = engine.find_safe_workflows(goal="run mixed-risk deploy")
    summary = result.matches[0]
    assert summary.step_count == 2
    assert summary.aggregate_risk_level == RiskLevel.HIGH
    assert summary.requires_confirmation is True


# ---------------------------------------------------------------------------
# Day 3 audit regressions
# ---------------------------------------------------------------------------


def test_query_policy_validator_rejects_non_positive_limits() -> None:
    """Regression: an earlier validator accepted `max_search_limit=0`. The
    engine then clamped every search request to `limit=0`, which failed
    Pydantic's `Field(ge=1)` on the response schema and surfaced as a 500
    to the caller. The validator now enforces positive ints up front so the
    contract can't ship in a broken state."""
    import copy

    from app.services.query_engine import _query_policy, _validate_query_policy

    base = copy.deepcopy(_query_policy())
    for key in ("default_search_limit", "max_search_limit"):
        for bad in (0, -5):
            broken = copy.deepcopy(base)
            broken[key] = bad
            with pytest.raises(ValueError, match=key):
                _validate_query_policy(broken)


def test_query_policy_validator_rejects_default_greater_than_max() -> None:
    import copy

    from app.services.query_engine import _query_policy, _validate_query_policy

    broken = copy.deepcopy(_query_policy())
    broken["default_search_limit"] = 999
    broken["max_search_limit"] = 50
    with pytest.raises(ValueError, match="default_search_limit"):
        _validate_query_policy(broken)


def test_query_policy_validator_rejects_bool_for_limit_fields() -> None:
    """bool is a subclass of int in Python (`True == 1`). The validator
    must reject it explicitly so `true` in JSON can't sneak in as `1`."""
    import copy

    from app.services.query_engine import _query_policy, _validate_query_policy

    for key in ("default_search_limit", "max_search_limit", "command_max_length"):
        broken = copy.deepcopy(_query_policy())
        broken[key] = True
        with pytest.raises(ValueError, match=key):
            _validate_query_policy(broken)
