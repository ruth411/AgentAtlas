"""Pydantic transport schemas for claims.

``KnowledgeClaim`` (defined here) is the wire / API / orchestrator shape.
``KnowledgeClaimRecord`` (in ``app.db.models``) is the SQLAlchemy
persistence shape. The two are converted at the ``ClaimStore`` boundary
in ``app.services.claim_store``. Pydantic schemas live here; ORM models
live there; do not cross-import the two in the same module.
"""

from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import ClaimType, ConfidenceBand, RiskLevel, VerificationStatus
from app.schemas.evidence import Evidence, EvidenceCreate
from app.schemas.risk import RiskAssessment


# Identifier shape applied to every server- and client-supplied ID across the
# project (claim_id, evidence_id, verification_id, run_id, artifact_id).
# Alphanumeric, underscore, hyphen, dot. Length cap matches the DB column.
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

# Caps that match the DB column types declared in `app/db/models.py`.
# Without these caps, Pydantic accepts payloads that SQLite silently truncates
# (in dev) and Postgres rejects with a 22001 (in prod), producing a portability
# bug that only surfaces on deploy.
TOOL_ID_MAX = 128
SUBMITTED_BY_MAX = 128
SUBJECT_MAX = 512
STATEMENT_MAX = 4000

# Evidence list cap. Confidence scoring already diminishes past two of any
# evidence type, and submit-time enforcement keeps memory + iteration cost
# bounded for every downstream consumer.
EVIDENCE_LIST_MAX = 50

# Workflow-step subjects must follow `workflow_id::step_number::action`.
# This is enforced at submit so malformed claims do not silently accumulate
# until the canonical compiler refuses them.
WORKFLOW_STEP_SUBJECT_PATTERN = re.compile(r"^[^:\s]+::[0-9]+::.+$")


def _require_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    return value


def _validate_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            "identifier must match [A-Za-z0-9_.-]{1,128}; got "
            f"{value!r}"
        )
    return value


class KnowledgeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=128)
    claim_type: ClaimType
    subject: str = Field(min_length=1, max_length=SUBJECT_MAX)
    statement: str = Field(min_length=1, max_length=STATEMENT_MAX)
    tool_id: str = Field(min_length=1, max_length=TOOL_ID_MAX)
    submitted_by: str = Field(min_length=1, max_length=SUBMITTED_BY_MAX)
    evidence: list[Evidence] = Field(max_length=EVIDENCE_LIST_MAX)
    risk_level: RiskLevel
    risk_level_classified: RiskLevel | None = None
    risk_assessment: RiskAssessment | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_band: ConfidenceBand | None = Field(default=None)
    created_at: datetime

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, value: str) -> str:
        return _validate_identifier(value) or value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)

    @model_validator(mode="after")
    def validate_workflow_step_subject(self) -> "KnowledgeClaim":
        if self.claim_type == ClaimType.WORKFLOW_STEP and not WORKFLOW_STEP_SUBJECT_PATTERN.fullmatch(
            self.subject
        ):
            raise ValueError(
                "workflow_step claim subject must match `workflow_id::step_number::action`"
            )
        return self


class ClaimCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str | None = Field(default=None, min_length=1, max_length=128)
    claim_type: ClaimType
    subject: str = Field(min_length=1, max_length=SUBJECT_MAX)
    statement: str = Field(min_length=1, max_length=STATEMENT_MAX)
    tool_id: str = Field(min_length=1, max_length=TOOL_ID_MAX)
    submitted_by: str = Field(min_length=1, max_length=SUBMITTED_BY_MAX)
    evidence: list[EvidenceCreate] = Field(max_length=EVIDENCE_LIST_MAX)
    risk_level: RiskLevel

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, value: str | None) -> str | None:
        return _validate_identifier(value)

    @model_validator(mode="after")
    def validate_workflow_step_subject(self) -> "ClaimCreate":
        if self.claim_type == ClaimType.WORKFLOW_STEP and not WORKFLOW_STEP_SUBJECT_PATTERN.fullmatch(
            self.subject
        ):
            raise ValueError(
                "workflow_step claim subject must match `workflow_id::step_number::action`"
            )
        return self
