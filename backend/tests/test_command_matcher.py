"""Stage 9: Strict command-matcher adversarial tests.

These tests pin every rule of the matcher contract:

- exact match beats everything
- prefix match requires a word boundary (no half-word matches)
- longest prefix wins
- ties resolved by verification level, then confidence, then created_at
- unaccepted claims are invisible
- empty / whitespace-only input returns None
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore
from app.services.command_matcher import (
    CommandMatch,
    match_command,
    normalize_command,
)
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


def _claim(
    *,
    subject: str,
    tool_id: str = "git",
    risk: RiskLevel = RiskLevel.LOW,
    created_at: datetime | None = None,
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject=subject,
        statement=f"{subject} test claim",
        tool_id=tool_id,
        submitted_by="matcher-test",
        evidence=[_evidence()],
        risk_level=risk,
        created_at=created_at or FIXED_TIME,
    )


def _accept(
    store: ClaimStore,
    claim: KnowledgeClaim,
    *,
    level: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED,
    confidence: float = 0.85,
    verified_at: datetime | None = None,
) -> None:
    """Persist a claim and stash an ACCEPTED VerificationResult at the
    requested verification level + confidence."""
    store.create(claim)
    breakdown = ConfidenceBreakdown(
        score=confidence,
        band=ConfidenceBand.STRONG if confidence >= 0.90 else ConfidenceBand.HIGH,
        components=[
            ConfidenceComponent(
                source="test", delta=confidence, reason="forced for matcher test"
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
            confidence_band=breakdown.band,
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment(claim.risk_level),
            reason_codes=["TEST_FORCE_L"],
            reasons=["forced for matcher test"],
            verified_at=verified_at or FIXED_TIME,
        )
    )


def _persist_with_status(
    store: ClaimStore, claim: KnowledgeClaim,
    *,
    status: VerificationStatus = VerificationStatus.PENDING,
    level: VerificationLevel = VerificationLevel.L1_SCHEMA_VALID,
    decision: OrchestratorDecision = OrchestratorDecision.PENDING_MORE_EVIDENCE,
    confidence: float = 0.3,
) -> None:
    """Persist a claim with a verification result at a specified
    status + level. Used to test the matcher's status/level filter rules."""
    store.create(claim)
    breakdown = ConfidenceBreakdown(
        score=confidence,
        band=ConfidenceBand.LOW if confidence < 0.55 else ConfidenceBand.MODERATE,
        components=[ConfidenceComponent(source="test", delta=confidence, reason="x")],
        caps_applied=[],
        penalties=[],
    )
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim.claim_id,
            decision=decision,
            verification_status=status,
            verification_level=level,
            confidence=confidence,
            confidence_band=breakdown.band,
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment(claim.risk_level),
            reason_codes=["TEST"],
            reasons=["forced for matcher test"],
            verified_at=FIXED_TIME,
        )
    )


# Backward-compat shim: existing tests still call _persist_unaccepted.
_persist_unaccepted = _persist_with_status


# ---------------------------------------------------------------------------
# normalize_command
# ---------------------------------------------------------------------------


def test_normalize_command_collapses_whitespace() -> None:
    assert normalize_command("  git   status \t --short ") == "git status --short"


def test_normalize_command_returns_empty_for_whitespace_only() -> None:
    assert normalize_command("   \t\n  ") == ""


# ---------------------------------------------------------------------------
# Exact match
# ---------------------------------------------------------------------------


def test_exact_match_returns_match(store: ClaimStore) -> None:
    claim = _claim(subject="git status")
    _accept(store, claim)
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is not None
    assert result.match_method == "exact"
    assert result.matched_subject == "git status"
    assert result.claim.claim_id == claim.claim_id


def test_exact_match_normalises_whitespace(store: ClaimStore) -> None:
    claim = _claim(subject="git status")
    _accept(store, claim)
    result = match_command(
        tool_id="git", command="  git\tstatus  ", store=store
    )
    assert result is not None
    assert result.match_method == "exact"


# ---------------------------------------------------------------------------
# Prefix match
# ---------------------------------------------------------------------------


def test_prefix_match_with_args(store: ClaimStore) -> None:
    claim = _claim(subject="gh repo delete", tool_id="github-cli", risk=RiskLevel.CRITICAL)
    _accept(store, claim)
    result = match_command(
        tool_id="github-cli",
        command="gh repo delete my-org/repo --yes",
        store=store,
    )
    assert result is not None
    assert result.match_method == "prefix"
    assert result.claim.claim_id == claim.claim_id


def test_prefix_match_requires_word_boundary(store: ClaimStore) -> None:
    """Regression: a claim for `gh repo` must NOT match `gh repos list`.

    Without the word-boundary rule, `gh repos list` would silently inherit
    the verdict of `gh repo`, which could be a different command with
    different risk."""
    claim = _claim(subject="gh repo", tool_id="github-cli")
    _accept(store, claim)
    result = match_command(
        tool_id="github-cli", command="gh repos list", store=store
    )
    assert result is None


def test_longest_prefix_wins(store: ClaimStore) -> None:
    """If both `gh repo` and `gh repo delete` are accepted claims and the
    user asks about `gh repo delete --yes`, we must pick the more specific
    `gh repo delete` (CRITICAL) — never the more general `gh repo` (LOW)."""
    broad = _claim(subject="gh repo", tool_id="github-cli", risk=RiskLevel.LOW)
    narrow = _claim(
        subject="gh repo delete", tool_id="github-cli", risk=RiskLevel.CRITICAL
    )
    _accept(store, broad, level=VerificationLevel.L3_RUNTIME_VERIFIED, confidence=0.95)
    _accept(store, narrow, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.80)

    result = match_command(
        tool_id="github-cli", command="gh repo delete my-org/repo", store=store
    )
    assert result is not None
    assert result.match_method == "prefix"
    assert result.claim.claim_id == narrow.claim_id
    assert result.claim.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# No match
# ---------------------------------------------------------------------------


def test_no_match_returns_none(store: ClaimStore) -> None:
    claim = _claim(subject="git status")
    _accept(store, claim)
    result = match_command(
        tool_id="git", command="git some-unknown-command", store=store
    )
    assert result is None


def test_no_claims_for_tool_returns_none(store: ClaimStore) -> None:
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is None


def test_empty_command_returns_none(store: ClaimStore) -> None:
    claim = _claim(subject="git status")
    _accept(store, claim)
    result = match_command(tool_id="git", command="   ", store=store)
    assert result is None


# ---------------------------------------------------------------------------
# ACCEPTED-only visibility
# ---------------------------------------------------------------------------


def test_claims_below_l2_invisible_to_matcher(store: ClaimStore) -> None:
    """Regardless of status, anything still at L1_schema_valid lacks
    source-verified evidence; the matcher must hide it."""
    pending = _claim(subject="git status")
    _persist_with_status(
        store, pending,
        status=VerificationStatus.PENDING,
        level=VerificationLevel.L1_SCHEMA_VALID,
    )
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is None


def test_pending_l2_claim_visible_to_matcher(store: ClaimStore) -> None:
    """Regression for the Stage 9 cross-cutting bug: single-evidence
    ingestion claims reach L2_source_verified but get
    verification_status=PENDING because their confidence (~0.45) is below
    the orchestrator's MODERATE acceptance band. Pre-fix the matcher's
    ACCEPTED-only filter hid them — breaking the entire ingestion-to-query
    pipeline. They must now be visible so verdicts are informative."""
    pending = _claim(subject="git status")
    _persist_with_status(
        store, pending,
        status=VerificationStatus.PENDING,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
        confidence=0.45,
    )
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is not None
    assert result.claim.claim_id == pending.claim_id
    assert result.verification_level == VerificationLevel.L2_SOURCE_VERIFIED


def test_requires_human_review_l2_claim_visible_to_matcher(
    store: ClaimStore,
) -> None:
    """High/critical risk claims with confidence < 0.85 get marked
    REQUIRES_HUMAN_REVIEW at L2 by the orchestrator. They are NOT explicit
    rejections; the matcher must surface them so the engine can flag the
    review-required state in the verdict."""
    needs_review = _claim(subject="gh repo delete", tool_id="github-cli", risk=RiskLevel.CRITICAL)
    _persist_with_status(
        store, needs_review,
        status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
        decision=OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
        confidence=0.6,
    )
    result = match_command(
        tool_id="github-cli", command="gh repo delete --yes", store=store,
    )
    assert result is not None
    assert result.claim.claim_id == needs_review.claim_id


def test_rejected_l2_claim_invisible_to_matcher(store: ClaimStore) -> None:
    """REJECTED is the orchestrator's explicit thumbs-down. Even at L2 the
    matcher must NOT surface it — surfacing a rejected claim as a verdict
    citation would undermine the orchestrator's authority."""
    rejected = _claim(subject="git status")
    _persist_with_status(
        store, rejected,
        status=VerificationStatus.REJECTED,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
        decision=OrchestratorDecision.REJECTED,
        confidence=0.4,
    )
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is None


def test_conflict_detected_l2_claim_invisible_to_matcher(
    store: ClaimStore,
) -> None:
    """CONFLICT_DETECTED means two sources disagree about this claim;
    surfacing one would be misleading. Matcher hides it even at L2."""
    conflicted = _claim(subject="git status")
    _persist_with_status(
        store, conflicted,
        status=VerificationStatus.CONFLICT_DETECTED,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
        decision=OrchestratorDecision.CONFLICT_DETECTED,
        confidence=0.5,
    )
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is None


# ---------------------------------------------------------------------------
# Tie-breaking
# ---------------------------------------------------------------------------


def test_higher_verification_level_wins_on_exact_ties(store: ClaimStore) -> None:
    older = _claim(subject="git status", created_at=FIXED_TIME)
    newer = _claim(subject="git status", created_at=FIXED_TIME + timedelta(hours=1))
    _accept(store, older, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.95)
    _accept(store, newer, level=VerificationLevel.L3_RUNTIME_VERIFIED, confidence=0.80)
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is not None
    assert result.claim.claim_id == newer.claim_id  # L3 beats L2 even at lower confidence


def test_higher_confidence_wins_at_same_level(store: ClaimStore) -> None:
    older = _claim(subject="git status", created_at=FIXED_TIME)
    newer = _claim(subject="git status", created_at=FIXED_TIME + timedelta(hours=1))
    _accept(store, older, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.95)
    _accept(store, newer, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.80)
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is not None
    assert result.claim.claim_id == older.claim_id  # higher confidence wins at same level


def test_most_recent_wins_at_full_tie(store: ClaimStore) -> None:
    older = _claim(subject="git status", created_at=FIXED_TIME)
    newer = _claim(subject="git status", created_at=FIXED_TIME + timedelta(hours=1))
    _accept(store, older, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.85)
    _accept(store, newer, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.85)
    result = match_command(tool_id="git", command="git status", store=store)
    assert result is not None
    assert result.claim.claim_id == newer.claim_id


# ---------------------------------------------------------------------------
# Cross-tool isolation
# ---------------------------------------------------------------------------


def test_claims_for_different_tool_are_invisible(store: ClaimStore) -> None:
    """A claim about `git status` for tool_id=git must not be returned for
    a query about tool_id=github-cli, even if the command text matches."""
    claim = _claim(subject="status", tool_id="git")
    _accept(store, claim)
    result = match_command(tool_id="github-cli", command="status", store=store)
    assert result is None


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


def test_batched_latest_verification_results_match_single_lookup(
    store: ClaimStore,
) -> None:
    """Regression: the matcher swapped per-claim `get_latest_verification_result`
    calls for the batched `get_latest_verification_results`. The two MUST
    return identical answers for the same input — otherwise the batching
    optimisation introduces a silent behaviour change."""
    claims: list[KnowledgeClaim] = []
    for i in range(10):
        c = _claim(
            subject=f"git multi-{i}",
            created_at=FIXED_TIME + timedelta(seconds=i),
        )
        # Each claim gets TWO verification results (L1 then L2). The latest
        # must always be L2; both methods must agree.
        store.create(c)
        claims.append(c)
        for j, level in enumerate(
            [VerificationLevel.L1_SCHEMA_VALID, VerificationLevel.L2_SOURCE_VERIFIED]
        ):
            from app.schemas.confidence import (
                ConfidenceBreakdown as _BD,
                ConfidenceComponent as _BC,
            )
            from app.services.ids import generate_verification_id as _vid

            bd = _BD(
                score=0.5 + j * 0.3,
                band=ConfidenceBand.HIGH,
                components=[_BC(source="t", delta=0.5, reason="x")],
                caps_applied=[],
                penalties=[],
            )
            store.save_verification_result(
                VerificationResult(
                    verification_id=_vid(),
                    claim_id=c.claim_id,
                    decision=OrchestratorDecision.ACCEPTED,
                    verification_status=VerificationStatus.ACCEPTED,
                    verification_level=level,
                    confidence=0.5 + j * 0.3,
                    confidence_band=ConfidenceBand.HIGH,
                    confidence_breakdown=bd,
                    risk_assessment=risk_assessment(c.risk_level),
                    reason_codes=["T"],
                    reasons=["t"],
                    verified_at=FIXED_TIME + timedelta(seconds=i * 10 + j),
                )
            )

    ids = [c.claim_id for c in claims]
    n1 = {cid: store.get_latest_verification_result(cid) for cid in ids}
    batched = store.get_latest_verification_results(ids)

    assert set(n1.keys()) == set(batched.keys())
    for cid in ids:
        assert (
            n1[cid].verification_id == batched[cid].verification_id
        ), f"verification_id mismatch for {cid}"
        assert (
            n1[cid].verification_level == batched[cid].verification_level
        ), f"level mismatch for {cid}"
    # Edge cases.
    assert store.get_latest_verification_results([]) == {}
    assert store.get_latest_verification_results(["claim_does_not_exist"]) == {}


def test_matcher_paginates_beyond_500_claims_per_tool(store: ClaimStore) -> None:
    """Regression: an earlier single-page implementation silently dropped any
    matching claim that fell past the 500th in creation order. With 30+ tools
    each carrying dozens of subcommands and flags this would have produced
    `no_match` verdicts for legitimately-verified claims as the graph grew.
    The fix paginates through ACCEPTED claims until the store is exhausted."""
    base = FIXED_TIME
    # Insert 500 noise claims with timestamps strictly EARLIER than the target.
    for i in range(500):
        noise = _claim(
            subject=f"git noise-{i}",
            created_at=base + timedelta(seconds=i),
        )
        _accept(store, noise, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.85)

    # The 501st claim is the actual target.
    target = _claim(
        subject="git rare-command",
        created_at=base + timedelta(seconds=10_000),
    )
    _accept(store, target, level=VerificationLevel.L2_SOURCE_VERIFIED, confidence=0.85)

    result = match_command(tool_id="git", command="git rare-command", store=store)
    assert result is not None
    assert result.claim.claim_id == target.claim_id


def test_command_match_exposes_verification_level_and_confidence(
    store: ClaimStore,
) -> None:
    claim = _claim(subject="git status")
    _accept(store, claim, level=VerificationLevel.L3_RUNTIME_VERIFIED, confidence=0.92)
    result = match_command(tool_id="git", command="git status", store=store)
    assert isinstance(result, CommandMatch)
    assert result.verification_level == VerificationLevel.L3_RUNTIME_VERIFIED
    assert result.confidence == 0.92
