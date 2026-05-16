from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import ClaimType, ConfidenceBand, RiskLevel, VerificationStatus
from app.schemas.evidence import Evidence, EvidenceCreate
from app.schemas.risk import RiskAssessment


def _require_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    return value


class KnowledgeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1)
    claim_type: ClaimType
    subject: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    submitted_by: str = Field(min_length=1)
    evidence: list[Evidence]
    risk_level: RiskLevel
    risk_level_classified: RiskLevel | None = None
    risk_assessment: RiskAssessment | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_band: ConfidenceBand | None = Field(default=None)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


class ClaimCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str | None = Field(default=None, min_length=1)
    claim_type: ClaimType
    subject: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    submitted_by: str = Field(min_length=1)
    evidence: list[EvidenceCreate]
    risk_level: RiskLevel
