from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.claim import ClaimCreate, KnowledgeClaim
from app.schemas.enums import ClaimType, OrchestratorDecision, RiskLevel, VerificationLevel, VerificationStatus
from app.schemas.evidence import Evidence
from app.schemas.verification import VerificationResult
from app.services.claim_store import (
    ClaimStore,
    DuplicateClaimError,
    DuplicateEvidenceError,
    EvidencePolicyError,
    ToolNotAllowedError,
    get_claim_store,
)
from app.services.evidence_trust import normalize_evidence_trust
from app.services.ids import generate_claim_id, generate_evidence_id
from app.services.orchestrator import CanonOrchestrator

router = APIRouter(tags=["claims"])


@router.post(
    "/claims",
    response_model=KnowledgeClaim,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_claim(
    claim: ClaimCreate,
    store: ClaimStore = Depends(get_claim_store),
) -> KnowledgeClaim:
    claim_id = claim.claim_id or generate_claim_id()
    evidence = [
        Evidence(
            **item.model_dump(exclude={"evidence_id"}),
            evidence_id=item.evidence_id or generate_evidence_id(),
        )
        for item in claim.evidence
    ]
    evidence = [normalize_evidence_trust(claim.tool_id, item) for item in evidence]

    normalized_claim = KnowledgeClaim(
        **claim.model_dump(exclude={"claim_id", "evidence"}),
        claim_id=claim_id,
        evidence=evidence,
        verification_status=VerificationStatus.PENDING,
        confidence=None,
        created_at=datetime.now(timezone.utc),
    )

    try:
        return store.create(normalized_claim)
    except ToolNotAllowedError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.TOOL_NOT_ALLOWED,
            message=str(exc),
            details={"tool_id": exc.tool_id, "allowed": exc.allowed},
        )
    except EvidencePolicyError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.REJECTED_PRIMARY_EVIDENCE,
            message="Submitted evidence violates the rejected-primary-evidence policy.",
            details={"violations": exc.violations},
        )
    except DuplicateClaimError as exc:
        raise_api_error(
            status.HTTP_409_CONFLICT,
            code=ErrorCode.CLAIM_ALREADY_EXISTS,
            message=str(exc),
            details={"claim_id": claim_id},
        )
    except DuplicateEvidenceError as exc:
        raise_api_error(
            status.HTTP_409_CONFLICT,
            code=ErrorCode.EVIDENCE_ALREADY_EXISTS,
            message=str(exc),
            details={"claim_id": claim_id},
        )


@router.get("/claims", response_model=list[KnowledgeClaim], responses=ERROR_RESPONSES)
def list_claims(
    tool_id: str | None = None,
    verification_status: VerificationStatus | None = None,
    risk_level: RiskLevel | None = None,
    risk_level_classified: RiskLevel | None = None,
    claim_type: ClaimType | None = None,
    submitted_by: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> list[KnowledgeClaim]:
    return store.list(
        tool_id=tool_id,
        verification_status=verification_status,
        risk_level=risk_level,
        risk_level_classified=risk_level_classified,
        claim_type=claim_type,
        submitted_by=submitted_by,
        limit=limit,
        offset=offset,
    )


@router.get("/claims/{claim_id}", response_model=KnowledgeClaim, responses=ERROR_RESPONSES)
def get_claim(
    claim_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> KnowledgeClaim:
    claim = store.get(claim_id)
    if claim is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CLAIM_NOT_FOUND,
            message=f"Claim '{claim_id}' does not exist.",
            details={"claim_id": claim_id},
        )
    return claim


@router.get(
    "/claims/{claim_id}/evidence",
    response_model=list[Evidence],
    responses=ERROR_RESPONSES,
)
def list_claim_evidence(
    claim_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> list[Evidence]:
    if store.get(claim_id) is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CLAIM_NOT_FOUND,
            message=f"Claim '{claim_id}' does not exist.",
            details={"claim_id": claim_id},
        )
    return store.list_evidence(claim_id=claim_id, limit=limit, offset=offset)


@router.post("/claims/{claim_id}/verify", response_model=VerificationResult, responses=ERROR_RESPONSES)
def verify_claim(
    claim_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> VerificationResult:
    claim = store.get(claim_id)
    if claim is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CLAIM_NOT_FOUND,
            message=f"Claim '{claim_id}' does not exist.",
            details={"claim_id": claim_id},
        )

    existing = store.get_latest_verification_result(claim_id)
    if existing is not None:
        return existing

    result = CanonOrchestrator(store).verify_claim(claim)
    return store.save_verification_result(result)


@router.get(
    "/claims/{claim_id}/verification",
    response_model=VerificationResult,
    responses=ERROR_RESPONSES,
)
def get_latest_claim_verification(
    claim_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> VerificationResult:
    if store.get(claim_id) is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CLAIM_NOT_FOUND,
            message=f"Claim '{claim_id}' does not exist.",
            details={"claim_id": claim_id},
        )

    result = store.get_latest_verification_result(claim_id)
    if result is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.VERIFICATION_RESULT_NOT_FOUND,
            message=f"Claim '{claim_id}' has no verification result.",
            details={"claim_id": claim_id},
        )
    return result


@router.get("/verification-results", response_model=list[VerificationResult], responses=ERROR_RESPONSES)
def list_verification_results(
    claim_id: str | None = None,
    decision: OrchestratorDecision | None = None,
    verification_status: VerificationStatus | None = None,
    verification_level: VerificationLevel | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> list[VerificationResult]:
    return store.list_verification_results(
        claim_id=claim_id,
        decision=decision,
        verification_status=verification_status,
        verification_level=verification_level,
        limit=limit,
        offset=offset,
    )


@router.get("/evidence/{evidence_id}", response_model=Evidence, responses=ERROR_RESPONSES)
def get_evidence(
    evidence_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> Evidence:
    evidence = store.get_evidence(evidence_id)
    if evidence is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.EVIDENCE_NOT_FOUND,
            message=f"Evidence '{evidence_id}' does not exist.",
            details={"evidence_id": evidence_id},
        )
    return evidence
