from collections.abc import Callable
from datetime import datetime, timezone

from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown, ConfidenceComponent
from app.schemas.enums import (
    ConfidenceBand,
    OrchestratorDecision,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.risk import RiskAssessment
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore
from app.services.confidence_scorer import (
    band_for_score,
    confidence_caps,
    compute_confidence_breakdown,
)
from app.services.evidence_policy import (
    evidence_policy_violations,
    has_trusted_source_evidence,
)
from app.services.evidence_trust import normalize_claim_evidence_trust
from app.services.ids import generate_verification_id
from app.services.risk_classifier import classify_action, is_higher_risk


_L2_PROMOTION_BANDS = frozenset(
    {
        ConfidenceBand.LOW,
        ConfidenceBand.MODERATE,
        ConfidenceBand.HIGH,
        ConfidenceBand.STRONG,
    }
)

_ACCEPTANCE_BANDS = frozenset(
    {
        ConfidenceBand.MODERATE,
        ConfidenceBand.HIGH,
        ConfidenceBand.STRONG,
    }
)


class CanonOrchestrator:
    def __init__(
        self,
        store: ClaimStore,
        *,
        verified_at: datetime | Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        if verified_at is None:
            self._verified_at = lambda: datetime.now(timezone.utc)
        elif isinstance(verified_at, datetime):
            self._verified_at = lambda: verified_at
        else:
            self._verified_at = verified_at

    def verify_claim(self, claim: KnowledgeClaim) -> VerificationResult:
        claim = normalize_claim_evidence_trust(claim)
        duplicate_claims = self._store.find_semantic_duplicates(claim)
        conflicting_claims = self._store.find_conflicts(claim)
        policy_violations = evidence_policy_violations(claim.evidence)
        risk_assessment = classify_action(claim.subject, claim.statement, claim.tool_id)

        def build_result(
            *,
            decision: OrchestratorDecision,
            status: VerificationStatus,
            level: VerificationLevel,
            breakdown: ConfidenceBreakdown,
            reason_codes: list[str],
            reasons: list[str],
        ) -> VerificationResult:
            return _result(
                claim,
                decision=decision,
                status=status,
                level=level,
                breakdown=breakdown,
                reason_codes=reason_codes,
                reasons=reasons,
                risk_assessment=risk_assessment,
                verified_at=self._verified_at(),
            )

        if duplicate_claims and not conflicting_claims:
            breakdown = compute_confidence_breakdown(claim)
            return build_result(
                decision=OrchestratorDecision.DUPLICATE,
                status=VerificationStatus.PENDING,
                level=VerificationLevel.L1_SCHEMA_VALID,
                breakdown=breakdown,
                reason_codes=["DUPLICATE_CLAIM"],
                reasons=[f"Duplicates accepted or submitted claim '{duplicate_claims[0].claim_id}'."],
            )

        if conflicting_claims:
            breakdown = compute_confidence_breakdown(claim, has_conflict=True)
            return build_result(
                decision=OrchestratorDecision.CONFLICT_DETECTED,
                status=VerificationStatus.CONFLICT_DETECTED,
                level=VerificationLevel.L1_SCHEMA_VALID,
                breakdown=breakdown,
                reason_codes=["CONFLICTING_CLAIM"],
                reasons=[f"Conflicts with claim '{conflicting_claims[0].claim_id}'."]
                + breakdown.penalties,
            )

        if not claim.evidence:
            return build_result(
                decision=OrchestratorDecision.PENDING_MORE_EVIDENCE,
                status=VerificationStatus.PENDING,
                level=VerificationLevel.L1_SCHEMA_VALID,
                breakdown=_empty_breakdown("Claim has no evidence; confidence is zero."),
                reason_codes=["MISSING_EVIDENCE"],
                reasons=["Claim has no evidence."],
            )

        if policy_violations:
            return build_result(
                decision=OrchestratorDecision.REJECTED,
                status=VerificationStatus.REJECTED,
                level=VerificationLevel.L1_SCHEMA_VALID,
                breakdown=_empty_breakdown(
                    "Claim evidence violates the rejected-primary-evidence policy."
                ),
                reason_codes=["REJECTED_PRIMARY_EVIDENCE"],
                reasons=policy_violations,
            )

        # Risk understatement only hard-fails to human review when the
        # classifier rates the action HIGH or CRITICAL. A submitter (or an
        # ingestion lane) lowballing a genuinely dangerous command must be
        # caught. But the risk model falls back to `medium` for any action
        # without an explicit rule, so a benign read-only claim submitted as
        # `low` is "understated" to `medium` purely by that fallback — not by
        # any real danger. Forcing those into human review buried ~2,000 valid
        # claims in the queue. For a medium reclassification we adopt the
        # classifier's verdict and fall through to the normal evidence/
        # confidence gates; the downstream HIGH/CRITICAL gate (below) still
        # guards anything actually dangerous.
        if is_higher_risk(risk_assessment.risk_level, claim.risk_level) and (
            risk_assessment.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        ):
            breakdown = compute_confidence_breakdown(claim)
            understated_cap = confidence_caps()["understated_risk"]
            capped_score = min(breakdown.score, understated_cap)
            cap_delta = round(capped_score - breakdown.score, 4)
            components = list(breakdown.components)
            if cap_delta < 0:
                components.append(
                    ConfidenceComponent(
                        source="cap:understated_risk",
                        delta=cap_delta,
                        reason=(
                            "Submitted risk lower than classified risk; score capped at "
                            f"{understated_cap:.2f}."
                        ),
                    )
                )
            understated_breakdown = breakdown.model_copy(
                update={
                    "score": capped_score,
                    "band": band_for_score(capped_score),
                    "components": components,
                    "caps_applied": breakdown.caps_applied
                    + [f"understated_risk:{understated_cap:.2f}"],
                    "penalties": breakdown.penalties
                    + [
                        "Submitted risk lower than classified risk; capped at "
                        f"{understated_cap:.2f}."
                    ],
                }
            )
            return build_result(
                decision=OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
                status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
                level=VerificationLevel.L1_SCHEMA_VALID,
                breakdown=understated_breakdown,
                reason_codes=["UNDERSTATED_RISK"],
                reasons=[
                    (
                        f"Submitted risk '{claim.risk_level}' is lower than classified risk "
                        f"'{risk_assessment.risk_level}'."
                    )
                ]
                + risk_assessment.reasons,
            )

        if not has_trusted_source_evidence(claim.evidence):
            breakdown = compute_confidence_breakdown(claim)
            return build_result(
                decision=OrchestratorDecision.PENDING_MORE_EVIDENCE,
                status=VerificationStatus.PENDING,
                level=VerificationLevel.L1_SCHEMA_VALID,
                breakdown=breakdown,
                reason_codes=["NO_TRUSTED_SOURCE_EVIDENCE"],
                reasons=["Claim lacks trusted source evidence."],
            )

        breakdown = compute_confidence_breakdown(claim)

        if breakdown.band not in _L2_PROMOTION_BANDS:
            return build_result(
                decision=OrchestratorDecision.PENDING_MORE_EVIDENCE,
                status=VerificationStatus.PENDING,
                level=VerificationLevel.L1_SCHEMA_VALID,
                breakdown=breakdown,
                reason_codes=["INSUFFICIENT_CONFIDENCE_FOR_L2"],
                reasons=[
                    "Confidence band 'none' blocks promotion from L1_schema_valid to L2_source_verified."
                ],
            )

        verification_level = VerificationLevel.L2_SOURCE_VERIFIED

        if claim.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} and breakdown.score < 0.85:
            return build_result(
                decision=OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
                status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
                level=verification_level,
                breakdown=breakdown,
                reason_codes=["HIGH_RISK_NEEDS_REVIEW"],
                reasons=[
                    "High or critical risk claim requires stronger evidence or human review."
                ]
                + breakdown.penalties,
            )

        if breakdown.band not in _ACCEPTANCE_BANDS:
            return build_result(
                decision=OrchestratorDecision.PENDING_MORE_EVIDENCE,
                status=VerificationStatus.PENDING,
                level=verification_level,
                breakdown=breakdown,
                reason_codes=["INSUFFICIENT_CONFIDENCE_FOR_ACCEPTANCE"],
                reasons=[
                    "Confidence band 'low' can promote to L2_source_verified but cannot be accepted."
                ],
            )

        if _surface_needs_informational_review(claim) and _distinct_evidence_streams(claim) < 2:
            return build_result(
                decision=OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
                status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
                level=verification_level,
                breakdown=breakdown,
                reason_codes=["INFORMATIONAL_SURFACE_NEEDS_REVIEW"],
                reasons=[
                    "Recipe/error surfaces stay informational until they have at least two distinct evidence streams."
                ] + breakdown.penalties,
            )

        return build_result(
            decision=OrchestratorDecision.ACCEPTED,
            status=VerificationStatus.ACCEPTED,
            level=verification_level,
            breakdown=breakdown,
            reason_codes=["TRUSTED_EVIDENCE_NO_CONFLICTS"],
            reasons=["Claim has trusted evidence and no detected conflicts."],
        )


def _empty_breakdown(reason: str) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(
        score=0.0,
        band=ConfidenceBand.NONE,
        components=[
            ConfidenceComponent(source="no_score", delta=0.0, reason=reason),
        ],
        caps_applied=["score_suppressed"],
        penalties=[],
    )


def _surface_needs_informational_review(claim: KnowledgeClaim) -> bool:
    if "-" not in claim.tool_id:
        return False
    surface = claim.tool_id.split("-", 1)[1]
    return surface in {"recipes", "errors"}


def _distinct_evidence_streams(claim: KnowledgeClaim) -> int:
    return len({(e.evidence_type, e.source_uri) for e in claim.evidence})


def _result(
    claim: KnowledgeClaim,
    *,
    decision: OrchestratorDecision,
    status: VerificationStatus,
    level: VerificationLevel,
    breakdown: ConfidenceBreakdown,
    reason_codes: list[str],
    reasons: list[str],
    risk_assessment: RiskAssessment,
    verified_at: datetime,
) -> VerificationResult:
    return VerificationResult(
        verification_id=generate_verification_id(),
        claim_id=claim.claim_id,
        decision=decision,
        verification_status=status,
        verification_level=level,
        confidence=breakdown.score,
        confidence_band=breakdown.band,
        confidence_breakdown=breakdown,
        risk_assessment=risk_assessment,
        reason_codes=reason_codes,
        reasons=reasons,
        verified_at=verified_at,
    )
