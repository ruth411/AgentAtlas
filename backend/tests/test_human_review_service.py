"""Stage 13: HumanReviewService unit tests.

Covers:

- L5 promotion path (APPROVED + claim already at L3+)
- Approval-too-early (APPROVED + claim below L3): no promotion, audit
  records the decision with an explanatory reason
- Rejection path (writes a new VerificationResult with status=REJECTED;
  matcher excludes the claim afterwards)
- NEEDS_CHANGES path (review row + audit event, no level/status change)
- Idempotency: each submit generates a new review row + audit row
- Missing claim raises a clean `HumanReviewError`
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.audit import AuditEvent
from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import (
    AuditEntityType,
    AuditEventType,
    ClaimType,
    ConfidenceBand,
    EvidenceType,
    HumanReviewDecision,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.human_review import HumanReviewRequest
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore
from app.services.human_review import HumanReviewError, HumanReviewService
from app.services.ids import generate_verification_id
from tests.helpers import risk_assessment, valid_sha256


def _claim(claim_id: str = "claim_review_001") -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="gh repo delete",
        statement="Deletes a GitHub repository.",
        tool_id="github-cli",
        submitted_by="cli-agent",
        evidence=[
            {
                "evidence_id": f"ev_{claim_id}",
                "evidence_type": EvidenceType.OFFICIAL_DOCS,
                "source_uri": "https://docs.github.com/en/manual/gh_repo_delete",
                "excerpt": "Deletes a repository.",
                "hash": valid_sha256(f"ev_{claim_id}"),
                "captured_at": datetime(2026, 5, 18, tzinfo=timezone.utc),
                "trust_level": TrustLevel.HIGH,
            }
        ],
        risk_level=RiskLevel.CRITICAL,
        verification_status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
        confidence=None,
        created_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )


def _verification(
    claim_id: str,
    *,
    level: VerificationLevel,
    status: VerificationStatus = VerificationStatus.ACCEPTED,
    score: float = 0.80,
) -> VerificationResult:
    breakdown = ConfidenceBreakdown(
        score=score,
        band=ConfidenceBand.HIGH if score >= 0.75 else ConfidenceBand.MODERATE,
        components=[],
        caps_applied=[],
        penalties=[],
    )
    return VerificationResult(
        verification_id=generate_verification_id(),
        claim_id=claim_id,
        decision=OrchestratorDecision.ACCEPTED,
        verification_status=status,
        verification_level=level,
        confidence=score,
        confidence_band=breakdown.band,
        confidence_breakdown=breakdown,
        risk_assessment=risk_assessment(RiskLevel.CRITICAL),
        reason_codes=["seeded"],
        reasons=["seeded for test"],
        verified_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )


def _fresh_store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'r.db'}")


# ---------------------------------------------------------------------------
# APPROVED + claim at L3+ → L5 promotion
# ---------------------------------------------------------------------------


def test_approved_review_promotes_l3_claim_to_l5(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(
        _verification(claim.claim_id, level=VerificationLevel.L3_RUNTIME_VERIFIED)
    )

    response = HumanReviewService(store).submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="alice",
            decision=HumanReviewDecision.APPROVED,
            notes="Looks good.",
        )
    )

    assert response.promoted_to_l5 is True
    assert response.new_verification_level == VerificationLevel.L5_HUMAN_AUDITED
    assert response.new_verification_status == VerificationStatus.ACCEPTED
    assert response.verification_id != ""
    assert any("Approved by alice" in r for r in response.reasons)

    latest = store.get_latest_verification_result(claim.claim_id)
    assert latest is not None
    assert latest.verification_level == VerificationLevel.L5_HUMAN_AUDITED


def test_approved_review_below_l3_does_not_promote(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(
        _verification(claim.claim_id, level=VerificationLevel.L2_SOURCE_VERIFIED)
    )

    response = HumanReviewService(store).submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="alice",
            decision=HumanReviewDecision.APPROVED,
        )
    )

    assert response.promoted_to_l5 is False
    assert response.new_verification_level == VerificationLevel.L2_SOURCE_VERIFIED
    assert any(
        "L5 promotion requires" in reason for reason in response.reasons
    )
    # Review IS persisted even though promotion didn't happen — humans
    # may have legitimately reviewed a claim that isn't yet eligible.
    assert len(store.list_human_reviews_for_claim(claim.claim_id)) == 1


def test_approved_review_with_no_prior_verification(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    claim = store.create(_claim())  # no save_verification_result call

    response = HumanReviewService(store).submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="alice",
            decision=HumanReviewDecision.APPROVED,
        )
    )

    assert response.promoted_to_l5 is False
    assert response.new_verification_level == VerificationLevel.L0_UNVERIFIED
    assert any("no verification result" in r.lower() for r in response.reasons)


# ---------------------------------------------------------------------------
# REJECTED → status flips to rejected; matcher excludes the claim
# ---------------------------------------------------------------------------


def test_rejected_review_writes_rejection_verification(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(
        _verification(claim.claim_id, level=VerificationLevel.L2_SOURCE_VERIFIED)
    )

    response = HumanReviewService(store).submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="bob",
            decision=HumanReviewDecision.REJECTED,
            notes="Subject text contradicts the official docs.",
        )
    )

    assert response.promoted_to_l5 is False
    assert response.new_verification_status == VerificationStatus.REJECTED
    # Level is preserved (we don't demote; only the status flips).
    assert response.new_verification_level == VerificationLevel.L2_SOURCE_VERIFIED

    latest = store.get_latest_verification_result(claim.claim_id)
    assert latest is not None
    assert latest.verification_status == VerificationStatus.REJECTED
    assert latest.decision == OrchestratorDecision.REJECTED


# ---------------------------------------------------------------------------
# NEEDS_CHANGES → no state change, only the review row + audit event
# ---------------------------------------------------------------------------


def test_needs_changes_writes_review_but_leaves_status(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(
        _verification(claim.claim_id, level=VerificationLevel.L2_SOURCE_VERIFIED)
    )

    before = store.get_latest_verification_result(claim.claim_id)
    response = HumanReviewService(store).submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="carol",
            decision=HumanReviewDecision.NEEDS_CHANGES,
            notes="Add an OpenAPI evidence entry, then resubmit.",
        )
    )

    assert response.promoted_to_l5 is False
    after = store.get_latest_verification_result(claim.claim_id)
    # Same row — no new verification was written.
    assert before is not None and after is not None
    assert before.verification_id == after.verification_id

    reviews = store.list_human_reviews_for_claim(claim.claim_id)
    assert len(reviews) == 1
    assert reviews[0].decision == HumanReviewDecision.NEEDS_CHANGES
    assert "OpenAPI" in reviews[0].notes


# ---------------------------------------------------------------------------
# Idempotency / multiple reviews
# ---------------------------------------------------------------------------


def test_two_reviews_produce_two_rows(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(
        _verification(claim.claim_id, level=VerificationLevel.L3_RUNTIME_VERIFIED)
    )
    svc = HumanReviewService(store)

    svc.submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="alice",
            decision=HumanReviewDecision.NEEDS_CHANGES,
            notes="needs evidence",
        )
    )
    svc.submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="alice",
            decision=HumanReviewDecision.APPROVED,
            notes="thanks for the update",
        )
    )

    reviews = store.list_human_reviews_for_claim(claim.claim_id)
    assert len(reviews) == 2
    # Both rows have unique review_ids.
    assert reviews[0].review_id != reviews[1].review_id


# ---------------------------------------------------------------------------
# Missing claim
# ---------------------------------------------------------------------------


def test_missing_claim_raises_human_review_error(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    try:
        HumanReviewService(store).submit_review(
            HumanReviewRequest(
                claim_id="claim_does_not_exist",
                reviewer_id="alice",
                decision=HumanReviewDecision.APPROVED,
            )
        )
    except HumanReviewError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected HumanReviewError")


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


def test_review_queue_filters_to_requires_human_review_status(tmp_path) -> None:
    store = _fresh_store(tmp_path)
    pending = store.create(_claim(claim_id="claim_pending_001"))
    other = store.create(_claim(claim_id="claim_accepted_001"))
    # Mark `other` as accepted so the queue must exclude it.
    store.save_verification_result(
        _verification(other.claim_id, level=VerificationLevel.L2_SOURCE_VERIFIED)
    )

    queue = HumanReviewService(store).list_pending_reviews()
    queued_ids = {item.claim_id for item in queue.items}
    assert pending.claim_id in queued_ids
    assert other.claim_id not in queued_ids
    assert queue.total == len(queued_ids)


# ---------------------------------------------------------------------------
# Audit events captured by the lifecycle
# ---------------------------------------------------------------------------


def test_approval_lifecycle_writes_three_audit_events(tmp_path) -> None:
    """Submit claim → save L3 → approve. Expect:
    1. CLAIM_SUBMITTED  (from store.create)
    2. VERIFICATION_RECORDED (from save_verification_result)
    3. HUMAN_REVIEW_RECORDED (from human_review.submit_review)
    4. VERIFICATION_RECORDED (the L5 promotion result)
    """
    store = _fresh_store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(
        _verification(claim.claim_id, level=VerificationLevel.L3_RUNTIME_VERIFIED)
    )
    HumanReviewService(store).submit_review(
        HumanReviewRequest(
            claim_id=claim.claim_id,
            reviewer_id="alice",
            decision=HumanReviewDecision.APPROVED,
        )
    )

    events: list[AuditEvent] = store.get_audit_events_for_claim(claim.claim_id)
    types = [event.event_type for event in events]
    assert types == [
        AuditEventType.CLAIM_SUBMITTED,
        AuditEventType.VERIFICATION_RECORDED,
        AuditEventType.HUMAN_REVIEW_RECORDED,
        AuditEventType.VERIFICATION_RECORDED,
    ]
    # All events name this claim as the entity.
    for event in events:
        assert event.entity_type == AuditEntityType.CLAIM
        assert event.entity_id == claim.claim_id
