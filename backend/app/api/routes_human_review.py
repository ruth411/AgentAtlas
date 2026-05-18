"""Stage 13 routes — human review queue + decision endpoint.

`POST /verification/human-review` files one `HumanReview` against a
claim. The service decides whether to promote, reject, or hold; the
response shows the outcome plus any reasons the dispatcher recorded.

`GET /verification/review-queue` paginates claims whose current status
is `REQUIRES_HUMAN_REVIEW`. The dashboard's review surface and the MCP
`list_pending_reviews` tool both call this endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.enums import RiskLevel
from app.schemas.human_review import (
    HumanReviewRequest,
    HumanReviewResponse,
    ReviewQueueResponse,
)
from app.services.claim_store import (
    ClaimStore,
    DuplicateHumanReviewError,
    get_claim_store,
)
from app.services.human_review import HumanReviewError, HumanReviewService


router = APIRouter(prefix="/verification", tags=["human-review"])


@router.post(
    "/human-review",
    response_model=HumanReviewResponse,
    responses=ERROR_RESPONSES,
)
def submit_human_review(
    request: HumanReviewRequest,
    store: ClaimStore = Depends(get_claim_store),
) -> HumanReviewResponse:
    try:
        return HumanReviewService(store).submit_review(request)
    except HumanReviewError as exc:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CLAIM_NOT_FOUND,
            message=str(exc),
            details={"claim_id": request.claim_id},
        )
    except DuplicateHumanReviewError as exc:
        # The service generates a fresh review_id every call; if this
        # surfaces it means the UUID generator collided (essentially
        # impossible) OR the same review_id was submitted twice through
        # a path that bypasses the service. Either way it's worth
        # surfacing as a clean 409 rather than a 500.
        raise_api_error(
            status.HTTP_409_CONFLICT,
            code=ErrorCode.HUMAN_REVIEW_ALREADY_EXISTS,
            message=str(exc),
            details={"claim_id": request.claim_id},
        )


@router.get(
    "/review-queue",
    response_model=ReviewQueueResponse,
    responses=ERROR_RESPONSES,
)
def list_review_queue(
    tool_id: str | None = Query(default=None, max_length=128),
    risk_level: RiskLevel | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> ReviewQueueResponse:
    return HumanReviewService(store).list_pending_reviews(
        tool_id=tool_id,
        risk_level=risk_level,
        limit=limit,
        offset=offset,
    )
