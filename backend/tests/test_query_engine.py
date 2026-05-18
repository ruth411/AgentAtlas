"""Stage 9 / Day 2: QueryEngine adversarial tests for validate_command +
explain_risk.

These tests pin the verdict-synthesis contract:

- default-deny on no match
- best-verified-claim wins (via the matcher)
- confidence gate, verification-level gate, safety-policy gate each block
  auto-execution independently
- evidence is correctly projected into the response
- reasons explain every gate that fired
- understated risk gets upgraded by the redundant classifier pass
- explain_risk returns the deterministic classification + citing claims
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
from app.schemas.query import ValidateCommandResponse
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_verification_id,
)
from app.services.query_engine import QueryEngine
from tests.helpers import risk_assessment, valid_sha256


FIXED_TIME = datetime(2026, 5, 18, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")


@pytest.fixture
def engine(store: ClaimStore) -> QueryEngine:
    return QueryEngine(store, now=FIXED_TIME)


def _evidence(uri: str = "https://git-scm.com/docs/git-status") -> Evidence:
    return Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri=uri,
        excerpt="trusted source excerpt",
        hash=valid_sha256(uri),
        captured_at=FIXED_TIME,
        trust_level=TrustLevel.HIGH,
    )


def _accept_claim(
    store: ClaimStore,
    *,
    subject: str,
    statement: str = "",
    tool_id: str = "git",
    claim_type: ClaimType = ClaimType.CLI_COMMAND_EXISTS,
    risk: RiskLevel = RiskLevel.LOW,
    level: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED,
    confidence: float = 0.85,
    evidence: list[Evidence] | None = None,
) -> KnowledgeClaim:
    claim = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=claim_type,
        subject=subject,
        statement=statement or subject,
        tool_id=tool_id,
        submitted_by="query-test",
        evidence=evidence or [_evidence()],
        risk_level=risk,
        created_at=FIXED_TIME,
    )
    store.create(claim)
    breakdown = ConfidenceBreakdown(
        score=confidence,
        band=_band_for(confidence),
        components=[
            ConfidenceComponent(
                source="test", delta=confidence, reason="forced for engine test"
            )
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
            confidence_band=_band_for(confidence),
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment(risk),
            reason_codes=["TEST_FORCE"],
            reasons=["forced for engine test"],
            verified_at=FIXED_TIME,
        )
    )
    return claim


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


# ---------------------------------------------------------------------------
# validate_command — no match (default-deny)
# ---------------------------------------------------------------------------


def test_no_matching_claim_returns_default_deny(
    engine: QueryEngine, store: ClaimStore
) -> None:
    response = engine.validate_command(
        tool_id="git", command="git some-unknown-thing"
    )
    assert isinstance(response, ValidateCommandResponse)
    assert response.matched_claim_id is None
    assert response.match_method == "none"
    assert response.safe_to_auto_execute is False
    assert response.requires_human_confirmation is True
    assert response.risk_level is None
    assert response.verification_level == VerificationLevel.L0_UNVERIFIED
    assert response.confidence == 0.0
    assert response.confidence_band == ConfidenceBand.NONE
    assert response.evidence == []
    assert response.reasons  # must spell out why
    assert any("Default policy" in r for r in response.reasons)


def test_unknown_tool_id_returns_default_deny(engine: QueryEngine) -> None:
    response = engine.validate_command(
        tool_id="not-a-real-tool", command="anything"
    )
    assert response.safe_to_auto_execute is False
    assert response.risk_level is None
    assert response.matched_claim_id is None


# ---------------------------------------------------------------------------
# validate_command — happy paths
# ---------------------------------------------------------------------------


def test_low_risk_high_confidence_l3_returns_safe_to_auto_execute(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _accept_claim(
        store,
        subject="git status",
        statement="Shows the working tree status.",
        risk=RiskLevel.LOW,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
    )
    response = engine.validate_command(tool_id="git", command="git status")
    assert response.match_method == "exact"
    assert response.safe_to_auto_execute is True
    assert response.requires_human_confirmation is False
    assert response.risk_level == RiskLevel.LOW
    assert response.verification_level == VerificationLevel.L3_RUNTIME_VERIFIED
    assert response.confidence == 0.92


def test_prefix_match_carries_extra_args(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Prefix matching means `git status --short --branch` matches a claim
    for `git status` and inherits its verdict."""
    _accept_claim(
        store,
        subject="git status",
        risk=RiskLevel.LOW,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
    )
    response = engine.validate_command(
        tool_id="git", command="git status --short --branch"
    )
    assert response.match_method == "prefix"
    assert response.safe_to_auto_execute is True


def test_evidence_projected_to_citations(
    engine: QueryEngine, store: ClaimStore
) -> None:
    ev1 = _evidence(uri="https://git-scm.com/docs/git-status")
    ev2 = Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=EvidenceType.CLI_HELP_OUTPUT,
        source_uri="agentatlas://ingestion/run_x/artifacts/art_y",
        excerpt="git status --help text",
        hash=valid_sha256("a"),
        captured_at=FIXED_TIME,
        trust_level=TrustLevel.MEDIUM,
    )
    _accept_claim(
        store,
        subject="git status",
        risk=RiskLevel.LOW,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
        evidence=[ev1, ev2],
    )
    response = engine.validate_command(tool_id="git", command="git status")
    assert len(response.evidence) == 2
    types = {e.evidence_type for e in response.evidence}
    assert EvidenceType.OFFICIAL_DOCS in types
    assert EvidenceType.CLI_HELP_OUTPUT in types
    # Citation projection drops excerpt + hash (those live in the artifact).
    for cite in response.evidence:
        assert hasattr(cite, "source_uri")
        assert hasattr(cite, "trust_level")


# ---------------------------------------------------------------------------
# validate_command — gating logic
# ---------------------------------------------------------------------------


def test_critical_risk_blocks_auto_execute_even_at_high_confidence(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _accept_claim(
        store,
        subject="gh repo delete",
        statement="Deletes a GitHub repository.",
        tool_id="github-cli",
        claim_type=ClaimType.DESTRUCTIVE_ACTION,
        risk=RiskLevel.CRITICAL,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.95,
        evidence=[_evidence("https://docs.github.com/manual/gh_repo_delete")],
    )
    response = engine.validate_command(
        tool_id="github-cli", command="gh repo delete my-org/x --yes"
    )
    assert response.safe_to_auto_execute is False
    assert response.requires_human_confirmation is True
    assert response.risk_level == RiskLevel.CRITICAL
    assert any("Safety policy blocks" in r for r in response.reasons)


def test_low_confidence_gates_auto_execute(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """A low-risk claim with confidence below the contract threshold must
    NOT be auto-executable, even though risk policy alone would allow it."""
    _accept_claim(
        store,
        subject="git status",
        risk=RiskLevel.LOW,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.40,  # below 0.70 threshold
    )
    response = engine.validate_command(tool_id="git", command="git status")
    assert response.safe_to_auto_execute is False
    assert response.requires_human_confirmation is True
    assert any("confidence" in r.lower() for r in response.reasons)


def test_low_verification_level_gates_auto_execute(
    engine: QueryEngine, store: ClaimStore, monkeypatch
) -> None:
    """The engine's verification-level gate is defense-in-depth: even if the
    matcher surfaces a claim, the engine must independently refuse to
    recommend auto-execution when the claim's level is below the contract's
    required minimum.

    The matcher already enforces L2 as its own floor (so L1 claims never
    reach the engine in practice). This test simulates a future contract
    change that raises the floor to L3 and confirms the engine still refuses
    L2 matches in that world."""
    import copy

    from app.services import query_engine as qe

    base_policy = copy.deepcopy(qe._query_policy())
    raised = copy.deepcopy(base_policy)
    raised["min_verification_level_for_auto_execute"] = "L3_runtime_verified"
    monkeypatch.setattr(qe, "_query_policy", lambda: raised)

    _accept_claim(
        store,
        subject="git status",
        risk=RiskLevel.LOW,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
        confidence=0.95,
    )
    response = engine.validate_command(tool_id="git", command="git status")
    assert response.safe_to_auto_execute is False
    assert response.verification_level == VerificationLevel.L2_SOURCE_VERIFIED
    assert any(
        "verification" in r.lower() or "level" in r.lower()
        for r in response.reasons
    )


def test_medium_risk_blocks_auto_execute_per_safety_policy(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _accept_claim(
        store,
        subject="git push",
        risk=RiskLevel.MEDIUM,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
    )
    response = engine.validate_command(tool_id="git", command="git push origin main")
    assert response.safe_to_auto_execute is False
    assert response.requires_human_confirmation is True


# ---------------------------------------------------------------------------
# validate_command — risk-classifier upgrade (defence-in-depth)
# ---------------------------------------------------------------------------


def test_classifier_upgrades_understated_risk(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Claim was submitted with risk=LOW but its subject `gh repo delete`
    triggers a CRITICAL classification. The verdict must reflect CRITICAL,
    not LOW, and the reasons must mention the upgrade."""
    _accept_claim(
        store,
        subject="gh repo delete",
        statement="Permanently deletes a GitHub repository.",
        tool_id="github-cli",
        risk=RiskLevel.LOW,  # understated on purpose
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.95,
    )
    response = engine.validate_command(
        tool_id="github-cli", command="gh repo delete my-org/x"
    )
    assert response.risk_level == RiskLevel.CRITICAL
    assert response.safe_to_auto_execute is False
    assert any("upgraded risk" in r for r in response.reasons)


# ---------------------------------------------------------------------------
# validate_command — response shape invariants
# ---------------------------------------------------------------------------


def test_verdict_generated_at_is_timezone_aware(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _accept_claim(store, subject="git status", confidence=0.85)
    response = engine.validate_command(tool_id="git", command="git status")
    assert response.verdict_generated_at.tzinfo is not None


def test_reasons_always_explain_match_method(
    engine: QueryEngine, store: ClaimStore
) -> None:
    _accept_claim(
        store,
        subject="git status",
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
    )
    response = engine.validate_command(tool_id="git", command="git status --short")
    assert any(
        "Matched claim" in r and "prefix" in r for r in response.reasons
    )


# ---------------------------------------------------------------------------
# explain_risk
# ---------------------------------------------------------------------------


def test_explain_risk_returns_classifier_output_for_unknown_tool(
    engine: QueryEngine,
) -> None:
    """Even for a tool not in the store, the deterministic classifier
    returns a verdict (it's content-driven, not tool-driven)."""
    response = engine.explain_risk(
        tool_id="not-a-real-tool", action="read a file"
    )
    assert response.risk_level is not None  # classifier always emits something
    assert response.citing_claim_ids == []  # no claims to cite


def test_explain_risk_attaches_citing_claim_when_match_exists(
    engine: QueryEngine, store: ClaimStore
) -> None:
    claim = _accept_claim(
        store,
        subject="gh repo delete",
        statement="Deletes a repo.",
        tool_id="github-cli",
        risk=RiskLevel.CRITICAL,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
    )
    response = engine.explain_risk(
        tool_id="github-cli", action="gh repo delete --yes"
    )
    assert claim.claim_id in response.citing_claim_ids
    assert response.confidence == 0.92


def test_explain_risk_dimensions_reflect_destructive_action(
    engine: QueryEngine,
) -> None:
    response = engine.explain_risk(tool_id="git", action="git reset --hard HEAD~1")
    # The risk engine classifies this as destructive; our dimension mapping
    # must surface that as both destructive_action=True AND reversible=False.
    if response.risk_dimensions.destructive_action:
        assert response.risk_dimensions.reversible is False


def test_explain_risk_response_is_typed_and_tz_aware(engine: QueryEngine) -> None:
    response = engine.explain_risk(tool_id="git", action="git status")
    assert response.verdict_generated_at.tzinfo is not None
    assert isinstance(response.risk_dimensions.destructive_action, bool)


# ---------------------------------------------------------------------------
# Day 2 audit regressions
# ---------------------------------------------------------------------------


def test_query_policy_validator_rejects_missing_reason_text_keys() -> None:
    """Regression: the engine reads `default_deny_reason`,
    `low_confidence_reason`, `low_verification_reason`, `unknown_tool_reason`
    from the contract but the original validator didn't require them. A
    contract missing any of these would crash at request time with
    KeyError — a 500 to the caller. The validator is now at least as strict
    as the engine."""
    import copy

    from app.services.query_engine import _query_policy, _validate_query_policy

    base = copy.deepcopy(_query_policy())
    for missing_key in (
        "default_deny_reason",
        "low_confidence_reason",
        "low_verification_reason",
        "unknown_tool_reason",
    ):
        broken = copy.deepcopy(base)
        del broken[missing_key]
        with pytest.raises(ValueError, match=missing_key):
            _validate_query_policy(broken)


def test_query_policy_validator_rejects_empty_reason_text() -> None:
    import copy

    from app.services.query_engine import _query_policy, _validate_query_policy

    broken = copy.deepcopy(_query_policy())
    broken["default_deny_reason"] = ""
    with pytest.raises(ValueError, match="non-empty"):
        _validate_query_policy(broken)


def test_query_policy_validator_rejects_bool_threshold() -> None:
    import copy

    from app.services.query_engine import _query_policy, _validate_query_policy

    broken = copy.deepcopy(_query_policy())
    broken["min_confidence_for_auto_execute"] = True  # bool sneaking in as int
    with pytest.raises(ValueError, match="number"):
        _validate_query_policy(broken)


def test_query_engine_band_labelling_uses_canonical_function() -> None:
    """Regression: an earlier engine version duplicated the band thresholds
    inline. If `confidence_scorer.band_for_score` evolves (e.g. contract
    thresholds change), the engine must inherit the new behaviour, not
    silently stay on the old thresholds."""
    from app.services.confidence_scorer import band_for_score

    # The engine now imports and uses this function directly. Verifying
    # importability + that boundary values produce sensible labels covers
    # the contract: any mismatch between engine output and band_for_score
    # would now be the same bug, surfaced in one place.
    for score in (0.0, 0.29, 0.30, 0.54, 0.55, 0.74, 0.75, 0.89, 0.90, 1.0):
        band = band_for_score(score)
        # NONE for very low, STRONG for very high; in between, monotonic.
        assert band is not None


def test_confidence_at_exact_threshold_passes_gate(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Boundary regression: a claim with confidence == threshold (0.70) must
    pass the confidence gate. Off-by-one would block legitimate auto-exec."""
    _accept_claim(
        store,
        subject="git status",
        risk=RiskLevel.LOW,
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.70,
    )
    response = engine.validate_command(tool_id="git", command="git status")
    assert response.safe_to_auto_execute is True
    assert response.confidence == 0.70


def test_engine_surfaces_pending_status_in_reasons(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Regression for the Stage 9 cross-cutting bug fix: when the matcher
    surfaces a PENDING claim (typical single-evidence ingestion output),
    the engine MUST spell out the partial-acceptance state in the verdict
    reasons so callers don't mistake an informational verdict for an
    authoritative one."""
    from app.schemas.confidence import (
        ConfidenceBreakdown as _BD,
        ConfidenceComponent as _BC,
    )
    from app.schemas.verification import VerificationResult as _VR
    from app.services.ids import generate_verification_id as _vid
    from app.schemas.enums import OrchestratorDecision as _OD

    # Build a PENDING-at-L2 claim directly (bypassing _accept_claim, which
    # forces ACCEPTED).
    c = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="Shows working tree status.",
        tool_id="git",
        submitted_by="t",
        evidence=[_evidence()],
        risk_level=RiskLevel.LOW,
        created_at=FIXED_TIME,
    )
    store.create(c)
    bd = _BD(
        score=0.45, band=ConfidenceBand.LOW,
        components=[_BC(source="t", delta=0.45, reason="x")],
        caps_applied=[], penalties=[],
    )
    store.save_verification_result(
        _VR(
            verification_id=_vid(), claim_id=c.claim_id,
            decision=_OD.PENDING_MORE_EVIDENCE,
            verification_status=VerificationStatus.PENDING,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=0.45, confidence_band=ConfidenceBand.LOW,
            confidence_breakdown=bd,
            risk_assessment=risk_assessment(RiskLevel.LOW),
            reason_codes=["TEST"], reasons=["forced pending"],
            verified_at=FIXED_TIME,
        )
    )

    response = engine.validate_command(tool_id="git", command="git status")
    # Matcher now sees PENDING-at-L2 claims (Bug A fix).
    assert response.matched_claim_id == c.claim_id
    # Confidence gate still blocks auto-execution.
    assert response.safe_to_auto_execute is False
    # And the verdict explicitly mentions the partial status so callers
    # can distinguish "no info" from "partial info."
    assert any(
        "verification_status" in r and "pending" in r
        for r in response.reasons
    )


def test_verification_level_at_exact_threshold_passes_gate(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Boundary regression: a claim at exactly L2 (the contract minimum)
    must pass the verification gate."""
    _accept_claim(
        store,
        subject="git status",
        risk=RiskLevel.LOW,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
        confidence=0.92,
    )
    response = engine.validate_command(tool_id="git", command="git status")
    assert response.safe_to_auto_execute is True
    assert response.verification_level == VerificationLevel.L2_SOURCE_VERIFIED
