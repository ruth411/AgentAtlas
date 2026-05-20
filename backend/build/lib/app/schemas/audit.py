"""Stage 13: append-only audit event schema.

Every state-changing operation in AgentAtlas emits one `AuditEvent` —
claim submissions, verification results (including L3/L5 promotions),
human reviews, canonical spec publications. The audit log is the source
of truth for "what happened, when, and at whose direction".

Append-only by contract: services only INSERT. There is no `update` or
`delete` method on `AuditLogger`. `tests/test_audit_log.py` enforces
that no service path mutates an existing event.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import AuditEntityType, AuditEventType


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            "identifier must match [A-Za-z0-9_.-]{1,128}; got "
            f"{value!r}"
        )
    return value


def _require_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value


class AuditEvent(BaseModel):
    """One immutable row in the audit log.

    `details` is a free-form JSON object so different event types can
    record different shapes (e.g. a verification event records the new
    level + decision; a review event records the decision + notes). The
    `event_type` discriminates which keys callers should expect.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=128)
    event_type: AuditEventType
    entity_type: AuditEntityType
    entity_id: str = Field(min_length=1, max_length=128)
    actor: str = Field(min_length=1, max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("event_id", "entity_id", "actor")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


class AuditEventQuery(BaseModel):
    """Filter params for `GET /audit/events`.

    All fields optional. Combining filters AND-s them. Pagination is
    deterministic via `(created_at ASC, event_id ASC)`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entity_type: AuditEntityType | None = None
    entity_id: str | None = Field(default=None, max_length=128)
    event_type: AuditEventType | None = None
    actor: str | None = Field(default=None, max_length=128)
    after: datetime | None = None
    before: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)

    @field_validator("entity_id", "actor")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_identifier(value)

    @field_validator("after", "before")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_tz_aware(value)


class AuditEventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    events: list[AuditEvent] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ClaimHistoryResponse(BaseModel):
    """Full audit trail for a single claim: every event the system
    recorded against it, in chronological order.

    The shape is opinionated — callers asked the question "what
    happened to this claim", and we answer with one list of
    `AuditEvent` records. The verification results + review records are
    also returned in their own arrays so consumers can render them
    directly without re-parsing `details`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=128)
    events: list[AuditEvent] = Field(default_factory=list)
    event_count: int = Field(ge=0)

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, value: str) -> str:
        return _validate_identifier(value)
