from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import (
    ConfidenceBand,
    OrchestratorDecision,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.risk import RiskAssessment


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verification_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
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
