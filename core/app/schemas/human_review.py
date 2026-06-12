"""Stage 13: human-review request / response models.

Submitting a review attaches one `HumanReview` to a claim and (on
APPROVED) promotes the latest verification result to `L5_HUMAN_AUDITED`.
The review record is immutable once written — corrections happen by
filing a new review, not editing the old one.
"""

from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import (
    HumanReviewDecision,
    VerificationLevel,
    VerificationStatus,
)


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


class HumanReviewRequest(BaseModel):
    """What a reviewer submits via the API or MCP `submit_review` tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=128)
    reviewer_id: str = Field(min_length=1, max_length=128)
    decision: HumanReviewDecision
    notes: str = Field(default="", max_length=4096)

    @field_validator("claim_id", "reviewer_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_identifier(value)


class HumanReview(BaseModel):
    """Persisted review record.

    Immutable after write. A correction is filed as a new review with
    `notes` that reference the prior `review_id` — the audit log keeps
    the chain.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    review_id: str = Field(min_length=1, max_length=128)
    claim_id: str = Field(min_length=1, max_length=128)
    reviewer_id: str = Field(min_length=1, max_length=128)
    decision: HumanReviewDecision
    notes: str = Field(default="", max_length=4096)
    reviewed_at: datetime

    @field_validator("review_id", "claim_id", "reviewer_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


class HumanReviewResponse(BaseModel):
    """Return shape for `POST /verification/human-review`.

    Tells the caller whether the review actually moved the needle.
    `promoted_to_l5` is `True` only when the prior verification level was
    `L3_RUNTIME_VERIFIED` or higher AND the decision was `APPROVED`.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    review: HumanReview
    promoted_to_l5: bool
    new_verification_level: VerificationLevel
    new_verification_status: VerificationStatus
    verification_id: str = Field(default="", max_length=128)
    reasons: list[str] = Field(default_factory=list)


class ReviewQueueItem(BaseModel):
    """Compact projection used by `GET /verification/review-queue`.

    Includes only the columns a reviewer needs to triage: which tool,
    which command/subject, current confidence + risk, when it was
    submitted, and why the orchestrator surfaced it for review."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=128)
    tool_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=512)
    statement: str = Field(min_length=1)
    risk_level: str = Field(min_length=1, max_length=32)
    verification_level: VerificationLevel
    verification_status: VerificationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    submitted_by: str = Field(min_length=1, max_length=128)
    created_at: datetime
    latest_verification_reasons: list[str] = Field(default_factory=list)


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    items: list[ReviewQueueItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
