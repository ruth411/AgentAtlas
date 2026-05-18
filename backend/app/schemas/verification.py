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


# -------- Stage 8: Runtime Verification --------


class RuntimeVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=128)
    submitted_by: str = Field(
        default="runtime-verifier-agent", min_length=1, max_length=128
    )

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, value: str) -> str:
        return _validate_identifier(value)


class RuntimeVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=128)
    verifier_kind: str = Field(default="", max_length=64)
    runtime_check_passed: bool = False
    promoted_to_l3: bool = False
    new_verification_level: VerificationLevel
    captured_artifact_id: str = Field(default="", max_length=128)
    verification_id: str = Field(default="", max_length=128)
    reasons: list[str] = Field(default_factory=list)
    skipped: bool = False
    skip_reason: str = Field(default="", max_length=256)


class BulkRuntimeVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    attempted: int = 0
    promoted: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[RuntimeVerificationResponse] = Field(default_factory=list)
