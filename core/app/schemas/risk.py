from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import RiskLevel


class RiskDimension(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    REMOTE_MUTATION = "remote_mutation"
    DESTRUCTIVE = "destructive"
    AUTH_SENSITIVE = "auth_sensitive"
    SECRET_EXPOSURE = "secret_exposure"
    COST_INCURRING = "cost_incurring"
    PRODUCTION = "production"
    FILESYSTEM = "filesystem"
    UNKNOWN = "unknown"


class RiskMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rule_id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    risk_level: RiskLevel
    dimensions: list[RiskDimension] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    matched_text: str = Field(min_length=1)


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel
    reasons: list[str] = Field(default_factory=list)
    requires_confirmation: bool
    safe_to_auto_execute: bool
    dimensions: list[RiskDimension] = Field(default_factory=list)
    matched_rule_ids: list[str] = Field(default_factory=list)
    matches: list[RiskMatch] = Field(default_factory=list)
