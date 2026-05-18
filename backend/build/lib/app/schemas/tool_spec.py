from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import RiskLevel, VerificationLevel


class PublicationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    source_claim_ids: list[str] = Field(default_factory=list)


class AuthRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    required: bool | None
    methods: list[str] = Field(default_factory=list)


class RiskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reads_private_data: bool | None = None
    mutates_local_state: bool | None = None
    mutates_remote_state: bool | None = None
    can_delete_resources: bool | None = None
    can_incur_cost: bool | None = None
    exposes_secrets: bool | None = None
    default_risk_level: RiskLevel


class CommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    risk_level: RiskLevel
    side_effects: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class ToolExample(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    example: str = Field(min_length=1)


class FailureMode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    condition: str = Field(min_length=1)
    recovery_steps: list[str] = Field(min_length=1)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_claim_ids: list[str] = Field(min_length=1)
    source_evidence_ids: list[str] = Field(min_length=1)
    compiled_at: datetime
    compiled_by: str = Field(min_length=1)
    verification_level: VerificationLevel


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    interfaces: list[str] = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    commands: list[CommandSpec] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    auth: AuthRequirement
    side_effects: list[str] = Field(default_factory=list)
    risk_profile: RiskProfile
    examples: list[ToolExample] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    failure_modes: list[FailureMode] = Field(default_factory=list)
    recovery_steps: list[str] = Field(default_factory=list)
    provenance: Provenance
    verification_level: VerificationLevel
    publication_issues: list[PublicationIssue] = Field(default_factory=list)
    content_hash: str | None = None

    @model_validator(mode="after")
    def validate_verification_alignment(self) -> "ToolSpec":
        if self.provenance.verification_level != self.verification_level:
            raise ValueError("provenance verification_level must match tool verification_level")
        return self
