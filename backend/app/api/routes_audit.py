"""Stage 13 routes — audit log query surface.

Two endpoints:

- `GET /audit/events` — paginated query against the append-only audit
  log, supporting filters by `entity_type`, `entity_id`, `event_type`,
  `actor`, and timestamp range.
- `GET /audit/claims/{claim_id}` — the full chronological audit trail
  for a single claim, returned as a `ClaimHistoryResponse`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.audit import (
    AuditEventListResponse,
    ClaimHistoryResponse,
)
from app.schemas.enums import AuditEntityType, AuditEventType
from app.services.claim_store import ClaimStore, get_claim_store


router = APIRouter(prefix="/audit", tags=["audit"])


# Reuse the identifier regex enforced on every other resource id; routes
# that take a claim_id from the path validate it with the same shape so
# callers get a structured 422 instead of an opaque 404 on a malformed
# id.
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"


@router.get(
    "/events",
    response_model=AuditEventListResponse,
    responses=ERROR_RESPONSES,
)
def list_audit_events(
    entity_type: AuditEntityType | None = Query(default=None),
    entity_id: str | None = Query(default=None, max_length=128),
    event_type: AuditEventType | None = Query(default=None),
    actor: str | None = Query(default=None, max_length=128),
    after: datetime | None = Query(default=None),
    before: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> AuditEventListResponse:
    if after is not None and after.tzinfo is None:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.AUDIT_EVENT_QUERY_INVALID,
            message="`after` must include a timezone offset.",
        )
    if before is not None and before.tzinfo is None:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.AUDIT_EVENT_QUERY_INVALID,
            message="`before` must include a timezone offset.",
        )
    events, total = store.list_audit_events(
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        actor=actor,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
    )
    return AuditEventListResponse(
        events=events, total=total, limit=limit, offset=offset
    )


@router.get(
    "/claims/{claim_id}",
    response_model=ClaimHistoryResponse,
    responses=ERROR_RESPONSES,
)
def get_claim_history(
    claim_id: str = Path(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    ),
    store: ClaimStore = Depends(get_claim_store),
) -> ClaimHistoryResponse:
    if store.get(claim_id) is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CLAIM_NOT_FOUND,
            message=f"Claim '{claim_id}' does not exist.",
            details={"claim_id": claim_id},
        )
    events = store.get_audit_events_for_claim(claim_id)
    return ClaimHistoryResponse(
        claim_id=claim_id,
        events=events,
        event_count=len(events),
    )
