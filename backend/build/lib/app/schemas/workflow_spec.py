from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import RiskLevel, VerificationLevel
from app.schemas.tool_spec import Provenance, PublicationIssue


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    step_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    command: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    risk_level: RiskLevel
    side_effects: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workflow_id: str = Field(min_length=1)
    goal: str | None = Field(default=None, min_length=1)
    tool_ids: list[str] = Field(min_length=1)
    steps: list[WorkflowStep] = Field(min_length=1)
    provenance: Provenance
    verification_level: VerificationLevel
    publication_issues: list[PublicationIssue] = Field(default_factory=list)
    content_hash: str | None = None

    @model_validator(mode="after")
    def validate_verification_alignment(self) -> "WorkflowSpec":
        if self.provenance.verification_level != self.verification_level:
            raise ValueError("provenance verification_level must match workflow verification_level")
        return self
