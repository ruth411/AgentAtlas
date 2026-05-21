"""SQLAlchemy ORM models — the persistence layer.

Naming convention: every persistence type in this module is suffixed
``Record`` (``KnowledgeClaimRecord``, ``VerificationResultRecord``, etc.).
The bare names (``KnowledgeClaim``, ``VerificationResult``, …) belong to
the Pydantic transport schemas in ``app.schemas.*``. The orchestrator,
ingestion lanes, and API routes convert between the two at the
``ClaimStore`` boundary.

If you import ``KnowledgeClaim`` from here and get an ``ImportError``,
you want either ``KnowledgeClaim`` from ``app.schemas.claim`` (transport
shape) or ``KnowledgeClaimRecord`` from this module (DB shape).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.schemas.enums import (
    AuditEntityType,
    AuditEventType,
    ClaimType,
    ConfidenceBand,
    EvidenceType,
    HumanReviewDecision,
    IngestionArtifactType,
    IngestionStatus,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)


def _allowed_values_sql(column_name: str, values: list[str]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted_values})"


class Base(DeclarativeBase):
    pass


class KnowledgeClaimRecord(Base):
    __tablename__ = "knowledge_claims"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("claim_type", [item.value for item in ClaimType]),
            name="ck_knowledge_claims_claim_type",
        ),
        CheckConstraint(
            _allowed_values_sql("risk_level", [item.value for item in RiskLevel]),
            name="ck_knowledge_claims_risk_level",
        ),
        CheckConstraint(
            _allowed_values_sql("verification_status", [item.value for item in VerificationStatus]),
            name="ck_knowledge_claims_verification_status",
        ),
        CheckConstraint(
            "confidence_band IS NULL OR "
            + _allowed_values_sql("confidence_band", [item.value for item in ConfidenceBand]),
            name="ck_knowledge_claims_confidence_band",
        ),
        CheckConstraint(
            "risk_level_classified IS NULL OR "
            + _allowed_values_sql("risk_level_classified", [item.value for item in RiskLevel]),
            name="ck_knowledge_claims_risk_level_classified",
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    tool_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_level_classified: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    risk_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_band: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    evidence: Mapped[list["EvidenceRecord"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        order_by="EvidenceRecord.evidence_id",
    )
    verification_results: Mapped[list["VerificationResultRecord"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        order_by="VerificationResultRecord.verified_at",
    )


class EvidenceRecord(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("evidence_type", [item.value for item in EvidenceType]),
            name="ck_evidence_evidence_type",
        ),
        CheckConstraint(
            _allowed_values_sql("trust_level", [item.value for item in TrustLevel]),
            name="ck_evidence_trust_level",
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_claims.claim_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    claim: Mapped[KnowledgeClaimRecord] = relationship(back_populates="evidence")


class VerificationResultRecord(Base):
    __tablename__ = "verification_results"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("decision", [item.value for item in OrchestratorDecision]),
            name="ck_verification_results_decision",
        ),
        CheckConstraint(
            _allowed_values_sql("verification_status", [item.value for item in VerificationStatus]),
            name="ck_verification_results_status",
        ),
        CheckConstraint(
            _allowed_values_sql("verification_level", [item.value for item in VerificationLevel]),
            name="ck_verification_results_level",
        ),
        CheckConstraint(
            _allowed_values_sql("confidence_band", [item.value for item in ConfidenceBand]),
            name="ck_verification_results_confidence_band",
        ),
    )

    verification_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_claims.claim_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verification_status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verification_level: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_band: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence_breakdown: Mapped[str] = mapped_column(Text, nullable=False)
    risk_assessment: Mapped[str] = mapped_column(Text, nullable=False)
    reason_codes: Mapped[str] = mapped_column(Text, nullable=False)
    reasons: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    claim: Mapped[KnowledgeClaimRecord] = relationship(back_populates="verification_results")


class CanonicalToolSpecRecord(Base):
    __tablename__ = "canonical_tool_specs"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("verification_level", [item.value for item in VerificationLevel]),
            name="ck_canonical_tool_specs_verification_level",
        ),
    )

    tool_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    compiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    compiled_by: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_claim_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    verification_level: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class CanonicalWorkflowSpecRecord(Base):
    __tablename__ = "canonical_workflow_specs"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("verification_level", [item.value for item in VerificationLevel]),
            name="ck_canonical_workflow_specs_verification_level",
        ),
    )

    workflow_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    compiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    compiled_by: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_claim_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    verification_level: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class IngestionRunRecord(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("status", [item.value for item in IngestionStatus]),
            name="ck_ingestion_runs_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tool_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    command: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)

    artifacts: Mapped[list["RawIngestionArtifactRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RawIngestionArtifactRecord.artifact_id",
    )


class RawIngestionArtifactRecord(Base):
    __tablename__ = "raw_ingestion_artifacts"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("artifact_type", [item.value for item in IngestionArtifactType]),
            name="ck_raw_ingestion_artifacts_type",
        ),
    )

    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("ingestion_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    run: Mapped[IngestionRunRecord] = relationship(back_populates="artifacts")


class HumanReviewRecord(Base):
    """Stage 13: persisted human-review decision.

    Append-only: once a row is written, the service layer never updates
    or deletes it. A correction is filed as a new review for the same
    claim with notes that reference the prior `review_id`."""

    __tablename__ = "human_reviews"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("decision", [item.value for item in HumanReviewDecision]),
            name="ck_human_reviews_decision",
        ),
    )

    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_claims.claim_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class AuditEventRecord(Base):
    """Stage 13: append-only audit log.

    Every state-changing operation in Ayiru (claim submission,
    verification results, human reviews, spec publication) emits one row.
    The service layer must only INSERT; there are no `update` or
    `delete` methods on the `AuditLogger`. The schema enforces no
    additional constraint, but `tests/test_audit_log.py` exercises the
    invariant that the service layer never mutates an existing event."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            _allowed_values_sql("event_type", [item.value for item in AuditEventType]),
            name="ck_audit_events_event_type",
        ),
        CheckConstraint(
            _allowed_values_sql("entity_type", [item.value for item in AuditEntityType]),
            name="ck_audit_events_entity_type",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class DocsFetchCacheRecord(Base):
    __tablename__ = "docs_fetch_cache"

    url: Mapped[str] = mapped_column(String(2048), primary_key=True)
    etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(128), nullable=True)
    body_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
