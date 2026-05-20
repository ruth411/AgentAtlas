"""Stage 13: human-review service.

Routes a `HumanReviewRequest` through the audit / verification pipeline:

- persists the review record (`HUMAN_REVIEW_RECORDED` audit event)
- applies the appropriate state change on the parent claim:
  * `APPROVED`     → promote to `L5_HUMAN_AUDITED` **only if** the claim
    has already reached `L3_RUNTIME_VERIFIED`. The level taxonomy is
    monotonic; we refuse to skip levels. Below L3, an approval is
    downgraded to `NEEDS_CHANGES` and surfaces a reason explaining why.
  * `REJECTED`     → write a new `VerificationResult` with
    `verification_status=REJECTED`. The matcher already excludes
    rejected claims, so the claim is removed from auto-execution
    surfaces immediately.
  * `NEEDS_CHANGES` → no level / status change; the claim stays in the
    review queue. The reviewer's notes are persisted on the
    `HumanReview` row and visible via the claim-history endpoint.

Every state change is wrapped by `ClaimStore.save_verification_result`
which itself emits a `VERIFICATION_RECORDED` audit event in the same
transaction. Net effect: one `submit_review` produces 1–2 audit events
that fully describe the decision and its consequences.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from app.schemas.confidence import ConfidenceBreakdown, ConfidenceComponent
from app.schemas.enums import (
    VERIFICATION_LEVEL_ORDER,
    ConfidenceBand,
    HumanReviewDecision,
    OrchestratorDecision,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.human_review import (
    HumanReview,
    HumanReviewRequest,
    HumanReviewResponse,
    ReviewQueueResponse,
)
from app.schemas.risk import RiskAssessment
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore
from app.services.ids import generate_human_review_id, generate_verification_id
from app.services.risk_classifier import classify_action


_MIN_LEVEL_FOR_L5 = VerificationLevel.L3_RUNTIME_VERIFIED


class HumanReviewError(ValueError):
    """Raised when the request references a claim that does not exist."""


class HumanReviewService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ---------------------------------------------------------------- public

    def submit_review(self, request: HumanReviewRequest) -> HumanReviewResponse:
        claim = self._store.get(request.claim_id)
        if claim is None:
            raise HumanReviewError(
                f"Claim '{request.claim_id}' does not exist."
            )
        prior = self._store.get_latest_verification_result(request.claim_id)

        review = HumanReview(
            review_id=generate_human_review_id(),
            claim_id=request.claim_id,
            reviewer_id=request.reviewer_id,
            decision=request.decision,
            notes=request.notes,
            reviewed_at=self._now(),
        )
        self._store.save_human_review(review)

        reasons: list[str] = []
        verification_id = ""
        promoted = False
        new_level = prior.verification_level if prior else VerificationLevel.L0_UNVERIFIED
        new_status = (
            prior.verification_status if prior else VerificationStatus.PENDING
        )

        if request.decision == HumanReviewDecision.APPROVED:
            if prior is None:
                reasons.append(
                    "Approval rejected: claim has no verification result on file "
                    "(orchestrator has not run yet)."
                )
            elif (
                VERIFICATION_LEVEL_ORDER[prior.verification_level]
                < VERIFICATION_LEVEL_ORDER[_MIN_LEVEL_FOR_L5]
            ):
                reasons.append(
                    f"Approval recorded but claim is at "
                    f"{prior.verification_level.value}; L5 promotion requires "
                    f"{_MIN_LEVEL_FOR_L5.value} or higher. The review is "
                    "preserved in the audit log; the orchestrator must "
                    "advance the claim to L3 first."
                )
            else:
                promoted_result = self._promote_to_l5(
                    claim_id=request.claim_id,
                    prior=prior,
                    reviewer_id=request.reviewer_id,
                )
                verification_id = promoted_result.verification_id
                new_level = promoted_result.verification_level
                new_status = promoted_result.verification_status
                promoted = True
                reasons.append(
                    f"Approved by {request.reviewer_id}; promoted to "
                    f"{new_level.value}."
                )
        elif request.decision == HumanReviewDecision.REJECTED:
            rejected_result = self._record_rejection(
                claim_id=request.claim_id,
                prior=prior,
                reviewer_id=request.reviewer_id,
                notes=request.notes,
            )
            verification_id = rejected_result.verification_id
            new_level = rejected_result.verification_level
            new_status = rejected_result.verification_status
            reasons.append(
                f"Rejected by {request.reviewer_id}; verification_status "
                "flipped to 'rejected' and the matcher will exclude this "
                "claim."
            )
        else:  # NEEDS_CHANGES
            reasons.append(
                f"{request.reviewer_id} requested changes; claim stays in "
                "the review queue. See HumanReview.notes."
            )

        return HumanReviewResponse(
            review=review,
            promoted_to_l5=promoted,
            new_verification_level=new_level,
            new_verification_status=new_status,
            verification_id=verification_id,
            reasons=reasons,
        )

    def list_pending_reviews(
        self,
        *,
        tool_id: str | None = None,
        risk_level=None,
        limit: int = 100,
        offset: int = 0,
    ) -> ReviewQueueResponse:
        items, total = self._store.list_pending_review_claims(
            tool_id=tool_id,
            risk_level=risk_level,
            limit=limit,
            offset=offset,
        )
        return ReviewQueueResponse(
            items=items, total=total, limit=limit, offset=offset
        )

    # --------------------------------------------------------------- helpers

    def _promote_to_l5(
        self,
        *,
        claim_id: str,
        prior: VerificationResult,
        reviewer_id: str,
    ) -> VerificationResult:
        breakdown = self._breakdown_with_review_bonus(prior.confidence_breakdown)
        claim = self._store.get(claim_id)
        # Re-classify risk from the claim so the new result is self-contained
        # (matches the orchestrator's pattern). Falls back to the prior
        # assessment if the claim is missing (defensive — get() above
        # already ran but a concurrent delete is theoretically possible).
        if claim is not None:
            risk_assessment: RiskAssessment = classify_action(
                claim.subject, claim.statement, claim.tool_id
            )
        else:
            risk_assessment = prior.risk_assessment

        result = VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L5_HUMAN_AUDITED,
            confidence=breakdown.score,
            confidence_band=ConfidenceBand(breakdown.band),
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment,
            reason_codes=["HUMAN_AUDITED"],
            reasons=[
                f"Promoted to L5_human_audited after approval by '{reviewer_id}'.",
                f"Prior level: {prior.verification_level.value}.",
            ],
            verified_at=self._now(),
        )
        return self._store.save_verification_result(result, actor=reviewer_id)

    def _record_rejection(
        self,
        *,
        claim_id: str,
        prior: VerificationResult | None,
        reviewer_id: str,
        notes: str,
    ) -> VerificationResult:
        claim = self._store.get(claim_id)
        if claim is not None:
            risk_assessment = classify_action(
                claim.subject, claim.statement, claim.tool_id
            )
        elif prior is not None:
            risk_assessment = prior.risk_assessment
        else:
            # Truly fresh-from-the-DB case — shouldn't reach here because
            # submit_review pre-checks the claim exists, but the type
            # system can't see that. Build a no-op assessment from the
            # classifier with empty inputs so the field is satisfied.
            risk_assessment = classify_action("", "", "")

        # Keep the existing level (rejection doesn't change L0..L5 — it
        # changes the *status*) so a future "un-reject" cycle has a
        # truthful provenance trail.
        level = prior.verification_level if prior else VerificationLevel.L0_UNVERIFIED
        breakdown = (
            prior.confidence_breakdown
            if prior is not None
            else _empty_breakdown()
        )
        result = VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim_id,
            decision=OrchestratorDecision.REJECTED,
            verification_status=VerificationStatus.REJECTED,
            verification_level=level,
            confidence=breakdown.score,
            confidence_band=ConfidenceBand(breakdown.band),
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment,
            reason_codes=["HUMAN_REJECTED"],
            reasons=[
                f"Rejected by reviewer '{reviewer_id}'.",
                f"Notes: {notes}" if notes else "Notes: (none provided)",
            ],
            verified_at=self._now(),
        )
        return self._store.save_verification_result(result, actor=reviewer_id)

    def _breakdown_with_review_bonus(
        self, prior: ConfidenceBreakdown
    ) -> ConfidenceBreakdown:
        """Apply a small +0.05 bonus to the prior confidence breakdown to
        reflect that a human has audited the claim. Bounded at 1.0 so we
        never overflow the contract.

        Keeping the bonus modest (0.05) preserves the existing band
        thresholds — a claim already at STRONG stays STRONG; one in HIGH
        may step up. The provenance of the bonus is recorded as one
        explicit `ConfidenceComponent` so the breakdown stays auditable.
        """
        bonus = 0.05
        new_score = round(min(1.0, prior.score + bonus), 4)
        components = list(prior.components) + [
            ConfidenceComponent(
                source="human_review:approval",
                delta=bonus,
                reason="Human reviewer approved; awarding +0.05.",
            )
        ]
        return prior.model_copy(
            update={
                "score": new_score,
                "band": _band_for_score(new_score),
                "components": components,
            }
        )


def _band_for_score(score: float) -> ConfidenceBand:
    """Inline of the confidence_scorer's band mapping to avoid a circular
    import. The thresholds match `contracts/confidence_model.v1.json`."""
    if score < 0.30:
        return ConfidenceBand.NONE
    if score < 0.55:
        return ConfidenceBand.LOW
    if score < 0.75:
        return ConfidenceBand.MODERATE
    if score < 0.90:
        return ConfidenceBand.HIGH
    return ConfidenceBand.STRONG


def _empty_breakdown() -> ConfidenceBreakdown:
    return ConfidenceBreakdown(
        score=0.0,
        band=ConfidenceBand.NONE,
        components=[],
        penalties=["no_prior_verification_result"],
    )
