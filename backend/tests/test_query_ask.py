"""Stage 17 — QueryEngine.ask() adversarial tests.

Pin the v0.2 retrieval contract:

- ACCEPTED-only filter (PENDING and REQUIRES_HUMAN_REVIEW excluded)
- token-overlap ranking with subject > tool_id > statement weighting
- all-stopword question → fallback_recommended=True with zero
  estimated_tokens_saved
- no-match question → same fallback shape
- empty graph → same fallback shape
- limit clamps at 1..20
- tool_id_hint narrows the candidate pool
- Answer.match_reason explains why the claim surfaced
- estimated_tokens_saved tracks _AVERAGE_WEB_SEARCH_TOKENS minus the
  serialised answer size
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

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
from app.schemas.query import Answer, AskResponse
from app.schemas.tool_spec import AuthRequirement, Provenance, RiskProfile, ToolSpec
from app.schemas.verification import VerificationResult
from app.schemas.workflow_spec import WorkflowSpec, WorkflowStep
from app.services.claim_store import ClaimStore
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_verification_id,
)
from app.services.query_engine import QueryEngine
from app.services.structured_knowledge_store import (
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)
from tests.helpers import risk_assessment, valid_sha256


FIXED_TIME = datetime(2026, 5, 21, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


@pytest.fixture
def engine(store: ClaimStore) -> QueryEngine:
    return QueryEngine(store, now=FIXED_TIME)


def _evidence(uri: str = "https://docs.example.com/test") -> Evidence:
    return Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri=uri,
        excerpt="trusted source excerpt",
        hash=valid_sha256(uri),
        captured_at=FIXED_TIME,
        trust_level=TrustLevel.HIGH,
    )


def _band_for(score: float) -> ConfidenceBand:
    if score < 0.30:
        return ConfidenceBand.NONE
    if score < 0.55:
        return ConfidenceBand.LOW
    if score < 0.75:
        return ConfidenceBand.MODERATE
    if score < 0.90:
        return ConfidenceBand.HIGH
    return ConfidenceBand.STRONG


def _add_claim(
    store: ClaimStore,
    *,
    subject: str,
    statement: str,
    tool_id: str = "docker",
    claim_type: ClaimType = ClaimType.CLI_COMMAND_EXISTS,
    risk: RiskLevel = RiskLevel.LOW,
    status: VerificationStatus = VerificationStatus.ACCEPTED,
    level: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED,
    confidence: float = 0.85,
) -> KnowledgeClaim:
    """Persist a claim AND its verification result so it's visible to
    ask() the same way an orchestrator-accepted claim would be.

    For non-ACCEPTED statuses we still write a result so the claim has a
    verification level on file — ask() filters by status BEFORE looking
    at level, so the status field is what gates exclusion.
    """
    claim = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=claim_type,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        submitted_by="ask-test",
        evidence=[_evidence(f"https://docs.example.com/{subject.replace(' ', '-')}")],
        risk_level=risk,
        created_at=FIXED_TIME,
    )
    store.create(claim)
    breakdown = ConfidenceBreakdown(
        score=confidence,
        band=_band_for(confidence),
        components=[
            ConfidenceComponent(source="test", delta=confidence, reason="forced for ask test")
        ],
        caps_applied=[],
        penalties=[],
    )
    # Map status to an appropriate decision so the result row is
    # contract-valid (the orchestrator's enum mapping).
    decision = {
        VerificationStatus.ACCEPTED: OrchestratorDecision.ACCEPTED,
        VerificationStatus.PENDING: OrchestratorDecision.PENDING_MORE_EVIDENCE,
        VerificationStatus.REQUIRES_HUMAN_REVIEW: OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
        VerificationStatus.REJECTED: OrchestratorDecision.REJECTED,
        VerificationStatus.CONFLICT_DETECTED: OrchestratorDecision.CONFLICT_DETECTED,
    }[status]
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim.claim_id,
            decision=decision,
            verification_status=status,
            verification_level=level,
            confidence=confidence,
            confidence_band=_band_for(confidence),
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment(risk),
            reason_codes=["TEST_FORCE"],
            reasons=["forced for ask test"],
            verified_at=FIXED_TIME,
        )
    )
    return claim


def _publish_tool_spec(store: ClaimStore, *, tool_id: str = "docker") -> None:
    store.save_canonical_tool_spec(
        ToolSpec(
            tool_id=tool_id,
            name=tool_id.title(),
            interfaces=["cli"],
            capabilities=["read", "execute"],
            commands=[],
            auth=AuthRequirement(required=False, methods=[]),
            risk_profile=RiskProfile(default_risk_level=RiskLevel.LOW),
            workflows=["workflow-docker-preview"],
            provenance=Provenance(
                source_claim_ids=["c1"],
                source_evidence_ids=["e1"],
                compiled_at=FIXED_TIME,
                compiled_by="ask-test",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            ),
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        )
    )


def _publish_workflow(
    store: ClaimStore, *, workflow_id: str = "workflow-docker-preview"
) -> None:
    store.save_canonical_workflow_spec(
        WorkflowSpec(
            workflow_id=workflow_id,
            goal="deploy preview environment",
            tool_ids=["docker"],
            steps=[
                WorkflowStep(
                    step_id="s1",
                    action="build preview image",
                    description="Build the preview image before deploy.",
                    risk_level=RiskLevel.LOW,
                )
            ],
            provenance=Provenance(
                source_claim_ids=["c1"],
                source_evidence_ids=["e1"],
                compiled_at=FIXED_TIME,
                compiled_by="ask-test",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            ),
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        )
    )


def _seed_structured_gh_subject(store: ClaimStore) -> None:
    _seed_structured_subject(
        store,
        subject_id="gh-pr-create",
        name="gh pr create (create a pull request)",
        family="gh",
    )


def _seed_structured_subject(
    store: ClaimStore,
    *,
    subject_id: str,
    name: str,
    family: str,
) -> None:
    structured = StructuredKnowledgeStore(session_factory=store._session_factory)
    structured.upsert_subject_graph(
        StructuredSubject(
            subject_id=subject_id,
            subject_kind="tool",
            name=name,
            family=family,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            provenance_claim_ids=[],
            created_at=FIXED_TIME,
            updated_at=FIXED_TIME,
        ),
        capabilities=[
            StructuredCapability(
                capability_id=f"cap-{subject_id}-invocation",
                subject_id=subject_id,
                capability_type="invocation",
                title=f"{name} invocation",
                detail={
                    "kind": "invocation",
                    "command": name.split(" (", 1)[0],
                    "source_url": "https://cli.github.com/manual/gh_pr_create",
                    "usage_signature": f"{name.split(' (', 1)[0]} [flags]",
                    "synopsis": name,
                    "argv_schema": {
                        "program": name.split()[0],
                        "subcommand_path": name.split(" (", 1)[0].split()[1:],
                        "positionals": [],
                    },
                    "flag_schema": [
                        {
                            "name": "--title",
                            "short": "-t",
                            "takes_value": True,
                            "value_name": "string",
                            "value_type": "string",
                            "repeatable": False,
                            "required": False,
                            "deprecated": False,
                            "inherited": False,
                            "description": "Title for the pull request",
                            "default": None,
                            "choices": [],
                        }
                    ],
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.99,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.MEDIUM,
                provenance_claim_ids=[],
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
            )
        ],
        constraints=[
            StructuredConstraint(
                constraint_id=f"constraint-{subject_id}-project",
                subject_id=subject_id,
                constraint_kind="auth_scope",
                detail={
                    "command": name.split(" (", 1)[0],
                    "source_url": "https://cli.github.com/manual/gh_pr_create",
                    "scope": "project",
                },
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
            )
        ],
        effects=[
            StructuredEffect(
                effect_id=f"effect-{subject_id}-mutation",
                subject_id=subject_id,
                effect_kind="mutation",
                destructive=False,
                reversible=False,
                mutates_remote_state=True,
                may_cost_money=False,
                may_expose_secrets=False,
                detail={
                    "command": name.split(" (", 1)[0],
                    "source_url": "https://cli.github.com/manual/gh_pr_create",
                    "synopsis": name,
                },
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ask_returns_top_match_for_a_realistic_question(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """The canonical headline: a natural-language question about a CLI
    surfaces the matching claim, with citations, no fallback."""
    target = _add_claim(
        store,
        subject="docker rm",
        statement="docker rm removes one or more containers from the local engine.",
        tool_id="docker",
        risk=RiskLevel.CRITICAL,
    )

    response = engine.ask(question="how do I delete a docker container")

    assert isinstance(response, AskResponse)
    assert response.answer_status == "accepted"
    assert response.fallback_recommended is False
    assert len(response.answers) >= 1
    assert response.answers[0].claim_id == target.claim_id
    assert response.answers[0].tool_id == "docker"
    assert response.answers[0].verification_status == VerificationStatus.ACCEPTED
    assert response.answers[0].risk_level == RiskLevel.CRITICAL
    # The match reason is debug surface — must say SOMETHING actionable.
    assert response.answers[0].match_reason
    # Evidence is projected (no full excerpt; just type + uri + trust).
    assert response.answers[0].evidence
    assert response.answers[0].evidence[0].source_uri.startswith("https://")


def test_ask_response_uses_timezone_aware_timestamp(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """generated_at must be tz-aware — the schema rejects naive datetimes
    on construction, so the engine has to comply."""
    _add_claim(store, subject="docker rm", statement="removes containers")
    response = engine.ask(question="docker rm container")
    assert response.generated_at.tzinfo is not None


def test_resolve_subject_and_subject_spec_return_canonical_projection(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _publish_tool_spec(store)
    _publish_workflow(store)

    response = engine.resolve_subject(subject_hint="docker")
    spec = engine.get_subject_spec(subject_id="docker")
    workflow_response = engine.resolve_subject(
        subject_hint="deploy preview environment",
        kind="workflow",
    )

    assert response.total >= 1
    assert any(match.subject_id == "docker" for match in response.matches)
    assert spec is not None
    assert spec.subject_id == "docker"
    assert spec.subject_kind == "tool"
    assert any(match.subject_kind == "workflow" for match in workflow_response.matches)


def test_resolve_subject_and_subject_spec_read_structured_subjects(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _seed_structured_gh_subject(store)

    response = engine.resolve_subject(subject_hint="gh pr create")
    spec = engine.get_subject_spec(subject_id="gh-pr-create")

    assert any(match.subject_id == "gh-pr-create" for match in response.matches)
    assert spec is not None
    assert spec.subject_id == "gh-pr-create"
    assert spec.interfaces == ["cli"]


def test_resolve_subject_family_hint_filters_without_replacing_query(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _seed_structured_gh_subject(store)
    _seed_structured_subject(
        store,
        subject_id="gh-pr-view",
        name="gh pr view",
        family="gh",
    )
    _seed_structured_subject(
        store,
        subject_id="ansible-ansible-windows-win-user-right",
        name="ansible.windows.win_user_right",
        family="ansible",
    )

    response = engine.resolve_subject(
        subject_hint="pr create",
        family_hint="gh",
        limit=1,
    )

    assert response.matches[0].subject_id == "gh-pr-create"
    assert response.total == 2


def test_resolve_subject_short_family_token_does_not_match_unrelated_suffixes(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _seed_structured_gh_subject(store)
    _seed_structured_subject(
        store,
        subject_id="ansible-ansible-windows-win-user-right",
        name="ansible.windows.win_user_right",
        family="ansible",
    )

    response = engine.resolve_subject(subject_hint="gh")

    assert any(match.subject_id == "gh-pr-create" for match in response.matches)
    assert all(
        match.subject_id != "ansible-ansible-windows-win-user-right"
        for match in response.matches
    )


def test_get_capabilities_constraints_and_effects_project_claim_types(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _add_claim(
        store,
        subject="docker login",
        statement="docker login requires valid registry credentials.",
        tool_id="docker",
        claim_type=ClaimType.AUTH_REQUIREMENT,
        risk=RiskLevel.LOW,
    )
    _add_claim(
        store,
        subject="docker volume rm",
        statement="docker volume rm permanently removes a local volume.",
        tool_id="docker",
        claim_type=ClaimType.SIDE_EFFECT,
        risk=RiskLevel.HIGH,
    )

    capabilities = engine.get_capabilities(subject_id="docker", accepted_only=False)
    constraints = engine.get_constraints(subject_id="docker")
    effects = engine.get_effects(subject_id="docker")

    assert capabilities.total >= 2
    assert any(cap.capability_type == "constraint" for cap in capabilities.capabilities)
    assert any(cap.claim_type == ClaimType.AUTH_REQUIREMENT for cap in constraints.constraints)
    assert any(cap.claim_type == ClaimType.SIDE_EFFECT for cap in effects.effects)
    assert effects.aggregate_risk_level == RiskLevel.HIGH


def test_get_capabilities_reads_structured_rows_before_projection(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _seed_structured_gh_subject(store)

    capabilities = engine.get_capabilities(subject_id="gh-pr-create")
    constraints = engine.get_constraints(subject_id="gh-pr-create")
    effects = engine.get_effects(subject_id="gh-pr-create")

    assert capabilities.total >= 1
    assert capabilities.capabilities[0].source == "structured"
    assert isinstance(capabilities.capabilities[0].detail, dict)
    assert constraints.total == 1
    assert constraints.constraints[0].source == "structured"
    assert effects.total == 1
    assert effects.effects[0].source == "structured"
    assert effects.aggregate_risk_level == RiskLevel.MEDIUM


def test_get_capabilities_can_force_structured_only_miss(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _add_claim(
        store,
        subject="docker login",
        statement="docker login requires valid registry credentials.",
        tool_id="docker",
        claim_type=ClaimType.AUTH_REQUIREMENT,
        risk=RiskLevel.LOW,
    )

    capabilities = engine.get_capabilities(
        subject_id="docker",
        accepted_only=False,
        accepted_only_structured=True,
    )

    assert capabilities.total == 0
    assert capabilities.accepted_only_structured is True
    assert capabilities.capabilities == []


def test_resolve_action_and_workflow_plan_project_structured_results(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _add_claim(
        store,
        subject="docker volume rm",
        statement="docker volume rm removes a local volume.",
        tool_id="docker",
        risk=RiskLevel.HIGH,
    )
    _publish_tool_spec(store)
    _publish_workflow(store)

    resolution = engine.resolve_action(
        subject_id="docker",
        action_intent="remove a local volume",
        command="docker volume rm my-volume",
    )
    plan = engine.get_workflow_plan(goal="deploy preview environment")

    assert resolution.resolution_mode == "command_match"
    assert resolution.subject_id == "docker"
    assert resolution.confidence >= 0.0
    assert plan.total >= 1
    assert any(item.workflow_id == "workflow-docker-preview" for item in plan.plans)


def test_resolve_action_reads_structured_subject_rows(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _seed_structured_gh_subject(store)

    resolution = engine.resolve_action(
        subject_id="gh-pr-create",
        action_intent="create a pull request",
        command="gh pr create --title hello",
    )

    assert resolution.resolution_mode == "command_match"
    assert resolution.top_capability is not None
    assert resolution.top_capability.source == "structured"
    assert isinstance(resolution.top_capability.detail, dict)
    assert resolution.fallback_recommended is False


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_ask_ranks_subject_match_above_statement_only_match(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Token overlap on the subject is worth 3× the same overlap in the
    statement prose. A claim where "delete" is in the subject should
    outrank one where "delete" only appears in the statement."""
    subject_winner = _add_claim(
        store,
        subject="docker delete",
        statement="A made-up command for the ranking test.",
        tool_id="docker",
    )
    _add_claim(
        store,
        subject="docker remove",
        statement="docker remove and delete are sometimes used interchangeably.",
        tool_id="docker",
    )

    response = engine.ask(question="how do I delete with docker")

    assert response.fallback_recommended is False
    assert response.answers[0].claim_id == subject_winner.claim_id


def test_ask_tool_id_match_outranks_statement_only_match(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """tool_id token hits carry weight 2.0 — a question that names the
    tool should prefer claims for that tool over an unrelated tool whose
    statement happens to mention the same word."""
    docker_claim = _add_claim(
        store,
        subject="docker ps",
        statement="lists running containers in the local engine.",
        tool_id="docker",
    )
    _add_claim(
        store,
        subject="vercel deploy",
        statement="deploys an app; some teams compare this to running containers with docker.",
        tool_id="vercel-cli",
    )

    response = engine.ask(question="what does docker do for running containers")

    assert response.fallback_recommended is False
    assert response.answers[0].claim_id == docker_claim.claim_id


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_ask_returns_fallback_for_all_stopword_question(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """A question made entirely of stop-words ('how do I') has no signal
    to rank on. The engine must return fallback=True rather than scoring
    against an empty filter."""
    _add_claim(store, subject="docker rm", statement="removes containers")

    response = engine.ask(question="how do I")

    assert response.answer_status == "miss"
    assert response.fallback_recommended is True
    assert response.answers == []
    assert response.estimated_tokens_saved == 0


def test_ask_returns_fallback_when_no_claim_overlaps(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Real graph, real question, but no lexical overlap. fallback=True
    with zero tokens saved — the agent should escalate to web_search."""
    _add_claim(store, subject="docker rm", statement="removes containers")

    response = engine.ask(question="aurora borealis quantum mechanics")

    assert response.answer_status == "miss"
    assert response.fallback_recommended is True
    assert response.answers == []
    assert response.estimated_tokens_saved == 0


def test_ask_returns_fallback_on_empty_graph(engine: QueryEngine) -> None:
    """A pristine database has zero candidates. ask() must still return
    the typed response shape (not raise, not 404)."""
    response = engine.ask(question="how do I delete a docker container")

    assert isinstance(response, AskResponse)
    assert response.answer_status == "miss"
    assert response.fallback_recommended is True
    assert response.answers == []


def test_ask_reads_structured_only_subjects_when_claim_graph_is_empty(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _seed_structured_gh_subject(store)

    response = engine.ask(
        question="how do I create a pull request title in gh",
        tool_id_hint="gh",
    )

    assert response.answer_status == "accepted"
    assert response.fallback_recommended is False
    assert response.answers
    top = response.answers[0]
    assert top.tool_id == "gh"
    assert top.claim_id == "cap-gh-pr-create-invocation"
    assert top.subject == "gh pr create (create a pull request)"
    assert top.verification_level == VerificationLevel.L3_RUNTIME_VERIFIED
    assert "title" in top.statement.lower() or "pull request" in top.statement.lower()


def test_ask_structured_only_unknown_hint_returns_honest_miss(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _seed_structured_gh_subject(store)

    response = engine.ask(
        question="how do I create a pull request",
        tool_id_hint="mythical-tool",
    )

    assert response.answer_status == "miss"
    assert response.fallback_recommended is True
    assert response.answers == []


# ---------------------------------------------------------------------------
# Acceptance filter
# ---------------------------------------------------------------------------


def test_ask_surfaces_pending_claims_as_informational_answers(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Pending claims are still visible to ask(), but only as explicitly
    informational answers rather than accepted ones."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        status=VerificationStatus.PENDING,
        level=VerificationLevel.L1_SCHEMA_VALID,
    )

    response = engine.ask(question="how do I delete a docker container")

    assert response.answer_status == "informational"
    assert response.fallback_recommended is False
    assert len(response.answers) >= 1
    assert response.answers[0].subject == "docker rm"
    assert response.answers[0].verification_status == VerificationStatus.PENDING


def test_ask_surfaces_requires_human_review_claims_as_informational_answers(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Review-needed claims are visible, but the response tier must make
    it explicit that they are not accepted knowledge yet."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        risk=RiskLevel.CRITICAL,
        status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
    )

    response = engine.ask(question="how do I delete a docker container")

    assert response.answer_status == "informational"
    assert response.fallback_recommended is False
    assert len(response.answers) >= 1
    assert response.answers[0].subject == "docker rm"
    assert (
        response.answers[0].verification_status
        == VerificationStatus.REQUIRES_HUMAN_REVIEW
    )


def test_ask_prefers_accepted_cli_answer_over_pending_recipe_answer(
    engine: QueryEngine, store: ClaimStore
) -> None:
    accepted = _add_claim(
        store,
        subject="docker build",
        statement="docker build builds an image from a Dockerfile.",
        tool_id="docker-cli",
        status=VerificationStatus.ACCEPTED,
    )
    _add_claim(
        store,
        subject="docker recipe: build an image with Dockerfile",
        statement="Use docker build -t myapp:dev . from the project root.",
        tool_id="docker-recipes",
        status=VerificationStatus.PENDING,
        level=VerificationLevel.L1_SCHEMA_VALID,
    )

    response = engine.ask(
        question="how do I build a docker image from a Dockerfile",
        tool_id_hint="docker",
    )

    assert response.answer_status == "accepted"
    assert response.fallback_recommended is False
    assert response.answers[0].claim_id == accepted.claim_id
    assert response.answers[0].verification_status == VerificationStatus.ACCEPTED


def test_ask_excludes_rejected_claims(engine: QueryEngine, store: ClaimStore) -> None:
    """Stage 19: REJECTED claims must NEVER surface in ask. The
    orchestrator explicitly said 'don't trust this' — surfacing it
    would undermine the audit trail."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        status=VerificationStatus.REJECTED,
        level=VerificationLevel.L1_SCHEMA_VALID,
    )

    response = engine.ask(question="how do I delete a docker container")

    assert response.fallback_recommended is True
    assert response.answers == []


def test_ask_marks_uncurated_tools_in_match_reason(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Stage 19: when a matched claim's tool_id is NOT in the v2
    contract's curated set, ask must tag the match_reason so the
    caller can distinguish L0 contributions from full-pipeline
    answers. The marker phrase is part of the contract — Stage 21's
    Python SDK reads it to set the ``Answer.is_useful`` heuristic."""
    # vercel-cli IS curated (in v2 contract); use a definitely-uncurated id.
    # We need to call store.create() directly with a non-curated tool_id
    # to bypass the curated-tools-only test fixtures.
    from app.schemas.claim import KnowledgeClaim
    from app.schemas.enums import ClaimType
    from app.services.ids import generate_claim_id
    from datetime import datetime, timezone

    uncurated_claim = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="kubectl scale",
        statement="kubectl scale deployment to N replicas",
        tool_id="kubectl",  # NOT in v2 curated contract
        submitted_by="ask-test",
        evidence=[_evidence("https://kubernetes.io/docs/reference/kubectl/")],
        risk_level=RiskLevel.MEDIUM,
        verification_status=VerificationStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    store.create(uncurated_claim)

    response = engine.ask(question="how do I scale a kubectl deployment")

    assert response.fallback_recommended is False
    assert len(response.answers) >= 1
    top = response.answers[0]
    assert top.tool_id == "kubectl"
    assert "uncurated" in top.match_reason.lower(), (
        f"expected 'uncurated' tag in match_reason, got: {top.match_reason!r}"
    )


# ---------------------------------------------------------------------------
# tool_id_hint
# ---------------------------------------------------------------------------


def test_ask_tool_id_hint_narrows_to_one_tool(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """When the caller already knows the tool, the hint must cut the
    candidate pool — claims for other tools shouldn't surface even if
    their tokens overlap better."""
    target = _add_claim(
        store,
        subject="docker delete",
        statement="A made-up docker subcommand for the hint test.",
        tool_id="docker",
    )
    _add_claim(
        store,
        subject="gh delete",
        statement="Stronger lexical overlap with the question, but wrong tool.",
        tool_id="github-cli",
    )

    response = engine.ask(question="delete something", tool_id_hint="docker")

    assert response.fallback_recommended is False
    assert all(a.tool_id == "docker" for a in response.answers)
    assert response.answers[0].claim_id == target.claim_id


def test_ask_tool_id_hint_expands_family_to_surfaces(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """A coarse hint like 'ffmpeg' (no exact match) must expand to all
    surface tool_ids whose name starts with 'ffmpeg-' — otherwise the
    Stage-20 five-surface decomposition silently breaks MCP clients
    that hint with the family name."""
    target_cli = _add_claim(
        store,
        subject="ffmpeg trim video",
        statement="ffmpeg -ss start -to end -c copy out.mp4 trims without re-encode.",
        tool_id="ffmpeg-cli",
    )
    target_recipes = _add_claim(
        store,
        subject="ffmpeg recipe: trim without re-encoding",
        statement="Cut a clip instantly with -c copy — see trim recipe.",
        tool_id="ffmpeg-recipes",
    )
    # Different family — must NOT surface under the ffmpeg hint.
    _add_claim(
        store,
        subject="docker trim image layers",
        statement="strong lexical match on 'trim' but wrong family.",
        tool_id="docker-cli",
    )

    response = engine.ask(question="trim video without re-encode", tool_id_hint="ffmpeg")

    assert response.fallback_recommended is False
    surfaced_ids = {a.tool_id for a in response.answers}
    assert surfaced_ids <= {"ffmpeg-cli", "ffmpeg-recipes"}
    surfaced_claim_ids = {a.claim_id for a in response.answers}
    assert {target_cli.claim_id, target_recipes.claim_id} & surfaced_claim_ids


def test_ask_tool_id_hint_exact_match_takes_precedence_over_prefix(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """If a tool_id matches the hint exactly, the matcher must use it
    directly — don't try to prefix-expand and pull in unrelated
    surfaces like 'docker-cli' when 'docker' itself exists."""
    target = _add_claim(
        store,
        subject="docker rm",
        statement="exact-tool-id claim.",
        tool_id="docker",
    )
    _add_claim(
        store,
        subject="docker-cli rm",
        statement="surface-tool-id claim.",
        tool_id="docker-cli",
    )

    response = engine.ask(question="docker rm container", tool_id_hint="docker")

    assert response.fallback_recommended is False
    assert all(a.tool_id == "docker" for a in response.answers)
    assert response.answers[0].claim_id == target.claim_id


def test_ask_tool_id_hint_unknown_returns_honest_miss(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Hint that matches no existing tool_id and no surface prefix
    must NOT silently widen to the whole graph — return the standard
    miss so the caller can fall back to web_search."""
    _add_claim(
        store,
        subject="docker rm",
        statement="some real claim — should not surface for an unrelated hint.",
        tool_id="docker-cli",
    )

    response = engine.ask(
        question="how do I deploy to mythical-tool",
        tool_id_hint="mythical-tool",
    )

    assert response.fallback_recommended is True
    assert response.answers == []


# ---------------------------------------------------------------------------
# Telemetry-adjacent
# ---------------------------------------------------------------------------


def test_ask_estimated_tokens_saved_is_positive_on_hit(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Heuristic: tokens-saved equals _AVERAGE_WEB_SEARCH_TOKENS (830)
    minus a quarter of the serialised JSON length. For a typical answer
    that nets ~300-700 saved tokens — definitely positive."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes one or more containers from the engine.",
    )

    response = engine.ask(question="docker rm container")

    assert response.fallback_recommended is False
    assert response.estimated_tokens_saved > 0


def test_ask_answer_carries_match_reason(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """match_reason is the debug surface — must always be populated and
    must reference at least one matched token from the question."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        tool_id="docker",
    )

    response = engine.ask(question="how do I rm a docker container")

    assert response.answers
    reason = response.answers[0].match_reason
    assert reason
    # The matcher should name at least one of the question's content
    # tokens (rm, docker, or container) in the reason.
    assert any(token in reason.lower() for token in ("rm", "docker", "container"))


# ---------------------------------------------------------------------------
# Answer shape
# ---------------------------------------------------------------------------


def test_ask_answer_projects_verification_level_from_latest_result(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """verification_level lives on the VerificationResult, not the claim
    record. ask() must batch-fetch the latest result for the top-N
    claims and surface its level on the Answer payload.

    Stage 22-stretch — Answer.confidence is now the match-relevance score
    (lexical-only here since the test fixture doesn't embed claims), not
    the orchestrator's claim-quality score. verification_level remains
    the canonical trust signal; confidence answers a different question
    ("does this claim address THIS question") and is computed per-ask.
    """
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
    )

    response = engine.ask(question="docker rm container")

    assert response.answers
    answer = response.answers[0]
    assert isinstance(answer, Answer)
    assert answer.verification_level == VerificationLevel.L3_RUNTIME_VERIFIED
    # Match-relevance score is normalised lexical (or hybrid lexical+semantic
    # when embeddings exist). Without an embedding in this fixture it's the
    # lexical score; we just require it to be positive and bounded.
    assert 0.0 < answer.confidence <= 1.0
    assert answer.confidence_band is not None


def test_ask_limit_caps_returned_answers(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """The engine clamps limit to 1..20 even if the schema's Field(le=20)
    is bypassed (e.g. when the engine is called programmatically). With
    limit=1, only the highest-scoring claim returns."""
    _add_claim(store, subject="docker rm", statement="removes containers")
    _add_claim(store, subject="docker stop", statement="stops a running container without removing it")

    response = engine.ask(question="docker container", limit=1)

    assert len(response.answers) == 1


# ---------------------------------------------------------------------------
# Audit regressions (caught in the 2026-05-21 senior-dev pass)
# ---------------------------------------------------------------------------


def test_short_command_tokens_survive_tokenization() -> None:
    """Audit regression. Earlier draft applied `_MIN_TOKEN_LENGTH = 3`
    which silently dropped half the Unix CLI vocabulary: `rm`, `ls`,
    `cd`, `mv`, `cp`, `du`, `df`, `ps`, `gh`, `jq`, `yq`, `bq`, `pg`,
    `vi`. The headline pitch is "agents look up CLI commands here
    instead of web_search" — if `rm` doesn't tokenize, the pitch is
    broken. The stop-word set already drops short noise particles
    (`a`, `i`, `is`, `to`, `do`); a length filter on top of it
    over-prunes."""
    from app.services.query_engine import _tokenize_question

    # Each of these would have returned `[]` or stripped the command
    # name under the buggy length filter. They MUST keep the command.
    assert "rm" in _tokenize_question("how do I use rm")
    assert "cd" in _tokenize_question("what does cd do")
    assert "ls" in _tokenize_question("when should I use ls -la")
    assert "gh" in _tokenize_question("how do I run gh repo list")
    assert "jq" in _tokenize_question("filter json with jq")
    # And the multi-token cases continue to work.
    assert _tokenize_question("docker rm container") == ["docker", "rm", "container"]


def test_repeated_keywords_do_not_inflate_score(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Audit regression. Earlier draft counted each occurrence of a
    question token as a separate hit, so duplicating keywords inflated
    the score by ~70% against the same claim. The fix dedupes the
    question token set before scoring.

    Strict invariant: adding duplicates of tokens already present in
    the question must NOT change the score. Two questions with the
    same unique-token set after dedup are equivalent."""
    from app.services.query_engine import _score_claim_for_question, _tokenize_question

    claim = _add_claim(
        store,
        subject="docker rm",
        statement="removes one or more containers from the engine",
        tool_id="docker",
    )
    base = _tokenize_question("delete a docker container")
    duplicated = _tokenize_question(
        "delete a docker docker docker container container container"
    )
    # Same unique-token set after dedup — must score identically.
    assert set(base) == set(duplicated)
    assert _score_claim_for_question(claim, base)[0] == (
        _score_claim_for_question(claim, duplicated)[0]
    )


def test_repeated_single_token_question_scores_same_as_one(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Stronger version of the prior test: the score for
    ``"docker docker docker"`` must equal the score for ``"docker"``
    against the same claim. They contain the same unique content; the
    matcher must treat them identically."""
    from app.services.query_engine import _score_claim_for_question, _tokenize_question

    claim = _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        tool_id="docker",
    )
    one = _tokenize_question("docker")
    many = _tokenize_question("docker docker docker docker docker")

    assert _score_claim_for_question(claim, one)[0] == (
        _score_claim_for_question(claim, many)[0]
    )
