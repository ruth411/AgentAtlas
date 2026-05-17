from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import (
    ConfidenceBand,
    OrchestratorDecision,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.risk import RiskAssessment

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            "identifier must match [A-Za-z0-9_.-]{1,128}; got "
            f"{value!r}"
        )
    return value


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verification_id: str = Field(min_length=1, max_length=128)
    claim_id: str = Field(min_length=1, max_length=128)
    decision: OrchestratorDecision
    verification_status: VerificationStatus
    verification_level: VerificationLevel
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBand
    confidence_breakdown: ConfidenceBreakdown
    risk_assessment: RiskAssessment
    reason_codes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    verified_at: datetime

    @field_validator("verification_id", "claim_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_identifier(value)
