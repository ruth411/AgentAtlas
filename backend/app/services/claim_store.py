from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
import json
import os
from typing import Any

from sqlalchemy import Engine, Select, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.db.models import (
    AuditEventRecord,
    CanonicalToolSpecRecord,
    CanonicalWorkflowSpecRecord,
    DocsFetchCacheRecord,
    EvidenceRecord,
    HumanReviewRecord,
    IngestionRunRecord,
    KnowledgeClaimRecord,
    RawIngestionArtifactRecord,
    VerificationResultRecord,
)
from app.db.session import SessionLocal, create_database_engine, init_db
from app.schemas.audit import AuditEvent
from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import (
    AuditEntityType,
    AuditEventType,
    ClaimType,
    ConfidenceBand,
    HumanReviewDecision,
    OrchestratorDecision,
    IngestionArtifactType,
    IngestionStatus,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.human_review import HumanReview, ReviewQueueItem
from app.schemas.evidence import Evidence
from app.schemas.ingestion import (
    CliIngestionSummary,
    DocsFetchCacheEntry,
    IngestionRun,
    RawIngestionArtifact,
)
from app.schemas.risk import RiskAssessment
from app.schemas.tool_spec import ToolSpec
from app.schemas.verification import VerificationResult
from app.schemas.workflow_spec import WorkflowSpec
from app.services.contract_paths import contract_path
from app.services.evidence_policy import evidence_policy_violations
from app.services.evidence_trust import normalize_claim_evidence_trust


@cache
def _curated_tool_ids() -> frozenset[str]:
    """Return the tool_ids the Stage 0 v2 contract marks ``curated: true``.

    Loaded from ``ayiru_stage_0.v2.json`` — both ``initial_tools`` and
    ``mcp_server_tools`` lists are scanned for entries with the
    ``curated`` flag set. These are the tools that get the full
    orchestrator path (claims → ACCEPTED → published ToolSpec →
    matched by ``validate_command``). Tools NOT in this set persist at
    ``L0_UNVERIFIED`` / ``PENDING`` and are visible to ``ask`` only.

    The earlier ``_stage_0_tool_ids()`` predecessor (v0.1) loaded v1
    and treated every entry as curated by definition. Stage 19's
    split moves the curation distinction into the contract itself so
    Stage 20's bulk ingest can add tools without contract bumps.
    """
    path = contract_path("ayiru_stage_0.v2.json")
    with path.open() as handle:
        data = json.load(handle)
    native = {
        tool["tool_id"]
        for tool in data["initial_tools"]
        if tool.get("curated") is True
    }
    mcp = {
        tool["tool_id"]
        for tool in data.get("mcp_server_tools", [])
        if tool.get("curated") is True
    }
    return frozenset(native | mcp)


def _strict_tool_lock_enabled() -> bool:
    """Stage 19.4: ``AYIRU_STRICT_TOOL_LOCK=1`` restores v0.1 behavior
    where ``ClaimStore.create()`` rejects any tool_id not in the curated
    set. Operators who want the original hard gate (e.g. compliance
    contexts where unknown tools must never silently land at L0) flip
    this. Default off — the relaxed L0 path is the v0.2 norm."""
    raw = os.environ.get("AYIRU_STRICT_TOOL_LOCK", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


class DuplicateClaimError(ValueError):
    """Raised when a claim id already exists in the store."""


class DuplicateEvidenceError(ValueError):
    """Raised when an evidence id already exists in the store."""


class EvidencePolicyError(ValueError):
    """Raised when a claim's evidence violates the rejected-primary-evidence policy."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = list(violations)
        super().__init__(
            "Claim evidence violates the rejected-primary-evidence policy: "
            + "; ".join(self.violations)
        )


class ToolNotAllowedError(ValueError):
    """Raised when a claim's tool_id is not in the locked Stage 0 contract."""

    def __init__(self, tool_id: str, allowed: frozenset[str]) -> None:
        self.tool_id = tool_id
        self.allowed = sorted(allowed)
        super().__init__(
            f"Tool '{tool_id}' is not in the Stage 0 tool contract. "
            f"Allowed tools: {self.allowed}."
        )


class DuplicateHumanReviewError(ValueError):
    """Raised when a `HumanReview.review_id` collides with an existing row."""


class ClaimStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        engine: Engine | None = None,
        database_url: str | None = None,
    ) -> None:
        if session_factory is not None:
            self._session_factory = session_factory
            return

        if engine is None and database_url is not None:
            engine = create_database_engine(database_url)

        if engine is None:
            # Production path: tables must exist via `alembic upgrade head`.
            # We deliberately do not call `create_all` here so the migration
            # history remains the single source of truth for schema.
            self._session_factory = SessionLocal
        else:
            # Ephemeral path (typically tests): create tables on the
            # provided engine for ergonomics.
            init_db(engine)
            self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def create(self, claim: KnowledgeClaim) -> KnowledgeClaim:
        claim = normalize_claim_evidence_trust(claim)

        # Stage 19: tool gating is now curated vs uncurated, not absolute.
        # - Curated tool_id  → full orchestrator path (verified, can publish
        #                      a ToolSpec, surfaces in validate_command).
        # - Uncurated tool_id → claim persists at L0_UNVERIFIED so it can
        #                       still surface in `ask`, but stays out of
        #                       the safety surface. This unlocks Stage 20's
        #                       bulk ingest without contract bumps.
        # - AYIRU_STRICT_TOOL_LOCK=1 restores the v0.1 hard reject for
        #   operators who need the original gate semantics.
        curated_tools = _curated_tool_ids()
        if claim.tool_id not in curated_tools and _strict_tool_lock_enabled():
            raise ToolNotAllowedError(claim.tool_id, curated_tools)

        violations = evidence_policy_violations(claim.evidence)
        if violations:
            raise EvidencePolicyError(violations)

        with self._session_factory() as session:
            if session.get(KnowledgeClaimRecord, claim.claim_id) is not None:
                raise DuplicateClaimError(f"Claim '{claim.claim_id}' already exists.")

            # Pre-check each evidence_id against the existing evidence table.
            # This replaces the fragile post-commit substring match against
            # SQLite's error message text. Two parallel writers can still
            # race here, but the post-commit IntegrityError below remains as
            # a defensive last line and is now correctly classified as a
            # generic conflict rather than misreported as a duplicate claim.
            for evidence in claim.evidence:
                if session.get(EvidenceRecord, evidence.evidence_id) is not None:
                    raise DuplicateEvidenceError(
                        f"Evidence id '{evidence.evidence_id}' already exists."
                    )

            session.add(_claim_to_record(claim))
            # Stage 13: same-transaction audit event. If the commit fails
            # below, the audit row rolls back with the claim — no phantom
            # log entries.
            _add_audit_event(
                session,
                event_type=AuditEventType.CLAIM_SUBMITTED,
                entity_type=AuditEntityType.CLAIM,
                entity_id=claim.claim_id,
                actor=claim.submitted_by,
                details={
                    "tool_id": claim.tool_id,
                    "claim_type": claim.claim_type.value,
                    "subject": claim.subject,
                    "risk_level": claim.risk_level.value,
                    "evidence_count": len(claim.evidence),
                },
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                # Concurrent inserts can still collide on either PK. Recheck
                # which constraint fired by looking up state, not by parsing
                # the driver error message.
                if any(
                    session.get(EvidenceRecord, evidence.evidence_id) is not None
                    for evidence in claim.evidence
                ):
                    raise DuplicateEvidenceError(
                        f"Evidence id attached to claim '{claim.claim_id}' already exists."
                    ) from exc
                if session.get(KnowledgeClaimRecord, claim.claim_id) is not None:
                    raise DuplicateClaimError(
                        f"Claim '{claim.claim_id}' already exists."
                    ) from exc
                raise
        return claim

    def list(
        self,
        *,
        tool_id: str | None = None,
        verification_status: VerificationStatus | None = None,
        risk_level: RiskLevel | None = None,
        risk_level_classified: RiskLevel | None = None,
        claim_type: ClaimType | None = None,
        submitted_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeClaim]:
        statement: Select[tuple[KnowledgeClaimRecord]] = select(KnowledgeClaimRecord).options(
            selectinload(KnowledgeClaimRecord.evidence)
        )
        statement = _maybe_filter(statement, tool_id, KnowledgeClaimRecord.tool_id.__eq__)
        statement = _maybe_filter(
            statement,
            verification_status.value if verification_status else None,
            KnowledgeClaimRecord.verification_status.__eq__,
        )
        statement = _maybe_filter(
            statement,
            risk_level.value if risk_level else None,
            KnowledgeClaimRecord.risk_level.__eq__,
        )
        statement = _maybe_filter(
            statement,
            risk_level_classified.value if risk_level_classified else None,
            KnowledgeClaimRecord.risk_level_classified.__eq__,
        )
        statement = _maybe_filter(
            statement,
            claim_type.value if claim_type else None,
            KnowledgeClaimRecord.claim_type.__eq__,
        )
        statement = _maybe_filter(statement, submitted_by, KnowledgeClaimRecord.submitted_by.__eq__)
        statement = statement.order_by(KnowledgeClaimRecord.created_at, KnowledgeClaimRecord.claim_id)
        statement = statement.limit(limit).offset(offset)

        with self._session_factory() as session:
            records = session.scalars(statement).all()
            return [_record_to_claim(record) for record in records]

    def get(self, claim_id: str) -> KnowledgeClaim | None:
        with self._session_factory() as session:
            record = session.scalars(
                select(KnowledgeClaimRecord)
                .where(KnowledgeClaimRecord.claim_id == claim_id)
                .options(selectinload(KnowledgeClaimRecord.evidence))
            ).one_or_none()
            return _record_to_claim(record) if record else None

    def find_semantic_duplicates(self, claim: KnowledgeClaim) -> list[KnowledgeClaim]:
        with self._session_factory() as session:
            records = session.scalars(
                select(KnowledgeClaimRecord)
                .where(KnowledgeClaimRecord.claim_id != claim.claim_id)
                .where(KnowledgeClaimRecord.tool_id == claim.tool_id)
                .where(KnowledgeClaimRecord.claim_type == claim.claim_type.value)
                .options(selectinload(KnowledgeClaimRecord.evidence))
                .order_by(KnowledgeClaimRecord.created_at, KnowledgeClaimRecord.claim_id)
            ).all()
            return [
                _record_to_claim(record)
                for record in records
                if _normalize_semantic_subject(record.subject) == _normalize_semantic_subject(claim.subject)
            ]

    def find_conflicts(self, claim: KnowledgeClaim) -> list[KnowledgeClaim]:
        candidates = self.find_semantic_duplicates(claim)
        return [
            candidate
            for candidate in candidates
            if candidate.statement != claim.statement or candidate.risk_level != claim.risk_level
        ]

    def list_evidence(
        self,
        *,
        claim_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Evidence]:
        statement = select(EvidenceRecord)
        if claim_id is not None:
            statement = statement.where(EvidenceRecord.claim_id == claim_id)
        statement = statement.order_by(EvidenceRecord.evidence_id).limit(limit).offset(offset)

        with self._session_factory() as session:
            records = session.scalars(statement).all()
            return [_record_to_evidence(record) for record in records]

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        with self._session_factory() as session:
            record = session.get(EvidenceRecord, evidence_id)
            return _record_to_evidence(record) if record else None

    def save_verification_result(
        self,
        result: VerificationResult,
        *,
        actor: str = "system",
    ) -> VerificationResult:
        """Persist a verification result and update the parent claim's
        denormalised columns.

        Stage 13: also writes one append-only audit row capturing the
        decision, level, and status. `actor` is recorded on the audit
        event; callers that know who triggered the verification
        (orchestrator, runtime verifier, human-review service) should
        pass it through. Defaults to ``"system"`` for legacy call sites.
        """
        with self._session_factory() as session:
            claim_record = session.get(KnowledgeClaimRecord, result.claim_id)
            if claim_record is None:
                raise ValueError(f"Claim '{result.claim_id}' does not exist.")

            session.add(_verification_result_to_record(result))
            claim_record.verification_status = result.verification_status.value
            claim_record.confidence = result.confidence
            claim_record.confidence_band = result.confidence_band.value
            claim_record.risk_level_classified = result.risk_assessment.risk_level.value
            claim_record.risk_assessment = result.risk_assessment.model_dump_json()
            _add_audit_event(
                session,
                event_type=AuditEventType.VERIFICATION_RECORDED,
                entity_type=AuditEntityType.CLAIM,
                entity_id=result.claim_id,
                actor=actor,
                details={
                    "verification_id": result.verification_id,
                    "decision": result.decision.value,
                    "verification_status": result.verification_status.value,
                    "verification_level": result.verification_level.value,
                    "confidence": result.confidence,
                    "confidence_band": result.confidence_band.value,
                    "risk_level": result.risk_assessment.risk_level.value,
                },
            )
            session.commit()
        return result

    def list_verification_results(
        self,
        *,
        claim_id: str | None = None,
        decision: OrchestratorDecision | None = None,
        verification_status: VerificationStatus | None = None,
        verification_level: VerificationLevel | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[VerificationResult]:
        statement = select(VerificationResultRecord)
        statement = _maybe_filter(statement, claim_id, VerificationResultRecord.claim_id.__eq__)
        statement = _maybe_filter(
            statement,
            decision.value if decision else None,
            VerificationResultRecord.decision.__eq__,
        )
        statement = _maybe_filter(
            statement,
            verification_status.value if verification_status else None,
            VerificationResultRecord.verification_status.__eq__,
        )
        statement = _maybe_filter(
            statement,
            verification_level.value if verification_level else None,
            VerificationResultRecord.verification_level.__eq__,
        )
        statement = statement.order_by(
            VerificationResultRecord.verified_at,
            VerificationResultRecord.verification_id,
        ).limit(limit).offset(offset)

        with self._session_factory() as session:
            records = session.scalars(statement).all()
            return [_record_to_verification_result(record) for record in records]

    def get_latest_verification_result(self, claim_id: str) -> VerificationResult | None:
        with self._session_factory() as session:
            record = session.scalars(
                select(VerificationResultRecord)
                .where(VerificationResultRecord.claim_id == claim_id)
                .order_by(
                    VerificationResultRecord.verified_at.desc(),
                    VerificationResultRecord.verification_id.desc(),
                )
                .limit(1)
            ).one_or_none()
            return _record_to_verification_result(record) if record else None

    def get_latest_verification_results(
        self, claim_ids: list[str]
    ) -> dict[str, VerificationResult]:
        """Batched equivalent of `get_latest_verification_result`.

        Returns the most recent `VerificationResult` for each claim_id in
        the input list, keyed by claim_id. Claims with no verification
        result are simply absent from the returned dict. Uses one SQL query
        regardless of how many claim_ids are passed in (vs. N queries for
        the single-claim version).

        Order-disambiguation matches the single-claim method exactly:
        `verified_at DESC, verification_id DESC` per claim_id.
        """
        if not claim_ids:
            return {}
        # Window-function approach: rank verification results per claim_id
        # by (verified_at DESC, verification_id DESC), take rank 1.
        # SQLite 3.25+ supports ROW_NUMBER() OVER (...); all modern Python
        # installs ship a recent-enough SQLite for this.
        from sqlalchemy import func

        ranked = (
            select(
                VerificationResultRecord,
                func.row_number()
                .over(
                    partition_by=VerificationResultRecord.claim_id,
                    order_by=(
                        VerificationResultRecord.verified_at.desc(),
                        VerificationResultRecord.verification_id.desc(),
                    ),
                )
                .label("_rn"),
            )
            .where(VerificationResultRecord.claim_id.in_(claim_ids))
            .subquery()
        )
        latest_alias = ranked.alias("latest")
        # Rebuild a proper ORM-mapped result by joining the subquery back
        # to the record table on the primary key. This keeps the row mapper
        # happy without us having to hand-construct the record from columns.
        with self._session_factory() as session:
            records = session.scalars(
                select(VerificationResultRecord)
                .join(
                    latest_alias,
                    (
                        VerificationResultRecord.verification_id
                        == latest_alias.c.verification_id
                    )
                    & (latest_alias.c._rn == 1),
                )
            ).all()
            return {
                record.claim_id: _record_to_verification_result(record)
                for record in records
            }

    def save_canonical_tool_spec(self, spec: ToolSpec) -> ToolSpec:
        # Retry once on IntegrityError so a TOCTOU race between get() and
        # add() (two concurrent publish requests for the same tool_id) does
        # not surface as a 500 to the caller. The retry path always finds
        # the row and falls through the update branch.
        for attempt in range(2):
            record = _tool_spec_to_record(spec)
            with self._session_factory() as session:
                existing = session.get(CanonicalToolSpecRecord, spec.tool_id)
                if existing is None:
                    session.add(record)
                    publish_kind = "created"
                else:
                    existing.spec_json = record.spec_json
                    existing.spec_hash = record.spec_hash
                    existing.content_hash = record.content_hash
                    existing.compiled_at = record.compiled_at
                    existing.compiled_by = record.compiled_by
                    existing.source_claim_ids_json = record.source_claim_ids_json
                    existing.verification_level = record.verification_level
                    publish_kind = "republished"
                _add_audit_event(
                    session,
                    event_type=AuditEventType.TOOL_SPEC_PUBLISHED,
                    entity_type=AuditEntityType.TOOL_SPEC,
                    entity_id=spec.tool_id,
                    actor=spec.provenance.compiled_by,
                    details={
                        "publish_kind": publish_kind,
                        "spec_hash": record.spec_hash,
                        "content_hash": spec.content_hash or "",
                        "verification_level": spec.verification_level.value,
                        "source_claim_count": len(spec.provenance.source_claim_ids),
                    },
                )
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    if attempt == 0:
                        continue
                    raise
                break
        return spec

    def get_canonical_tool_spec(self, tool_id: str) -> ToolSpec | None:
        with self._session_factory() as session:
            record = session.get(CanonicalToolSpecRecord, tool_id)
            return _record_to_tool_spec(record) if record else None

    def list_canonical_tool_specs(self, *, limit: int = 100, offset: int = 0) -> list[ToolSpec]:
        with self._session_factory() as session:
            records = session.scalars(
                select(CanonicalToolSpecRecord)
                .order_by(CanonicalToolSpecRecord.tool_id)
                .limit(limit)
                .offset(offset)
            ).all()
            return [_record_to_tool_spec(record) for record in records]

    def save_canonical_workflow_spec(self, spec: WorkflowSpec) -> WorkflowSpec:
        # Same TOCTOU retry pattern as save_canonical_tool_spec.
        for attempt in range(2):
            record = _workflow_spec_to_record(spec)
            with self._session_factory() as session:
                existing = session.get(CanonicalWorkflowSpecRecord, spec.workflow_id)
                if existing is None:
                    session.add(record)
                    publish_kind = "created"
                else:
                    existing.spec_json = record.spec_json
                    existing.spec_hash = record.spec_hash
                    existing.content_hash = record.content_hash
                    existing.compiled_at = record.compiled_at
                    existing.compiled_by = record.compiled_by
                    existing.source_claim_ids_json = record.source_claim_ids_json
                    existing.verification_level = record.verification_level
                    publish_kind = "republished"
                _add_audit_event(
                    session,
                    event_type=AuditEventType.WORKFLOW_SPEC_PUBLISHED,
                    entity_type=AuditEntityType.WORKFLOW_SPEC,
                    entity_id=spec.workflow_id,
                    actor=spec.provenance.compiled_by,
                    details={
                        "publish_kind": publish_kind,
                        "spec_hash": record.spec_hash,
                        "content_hash": spec.content_hash or "",
                        "verification_level": spec.verification_level.value,
                        "source_claim_count": len(spec.provenance.source_claim_ids),
                    },
                )
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    if attempt == 0:
                        continue
                    raise
                break
        return spec

    def get_canonical_workflow_spec(self, workflow_id: str) -> WorkflowSpec | None:
        with self._session_factory() as session:
            record = session.get(CanonicalWorkflowSpecRecord, workflow_id)
            return _record_to_workflow_spec(record) if record else None

    def list_canonical_workflow_specs(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[WorkflowSpec]:
        with self._session_factory() as session:
            records = session.scalars(
                select(CanonicalWorkflowSpecRecord)
                .order_by(CanonicalWorkflowSpecRecord.workflow_id)
                .limit(limit)
                .offset(offset)
            ).all()
            return [_record_to_workflow_spec(record) for record in records]

    def save_ingestion_run(self, run: IngestionRun) -> IngestionRun:
        record = _ingestion_run_to_record(run)
        with self._session_factory() as session:
            existing = session.get(IngestionRunRecord, run.run_id)
            if existing is None:
                session.add(record)
            else:
                existing.tool_id = record.tool_id
                existing.command = record.command
                existing.status = record.status
                existing.submitted_by = record.submitted_by
                existing.created_at = record.created_at
                existing.completed_at = record.completed_at
                existing.summary_json = record.summary_json
            session.commit()
        return run

    def list_ingestion_runs(self, *, limit: int = 100, offset: int = 0) -> list[IngestionRun]:
        with self._session_factory() as session:
            records = session.scalars(
                select(IngestionRunRecord)
                .order_by(
                    IngestionRunRecord.created_at,
                    IngestionRunRecord.run_id,
                )
                .limit(limit)
                .offset(offset)
            ).all()
            return [_record_to_ingestion_run(record) for record in records]

    def get_ingestion_run(self, run_id: str) -> IngestionRun | None:
        with self._session_factory() as session:
            record = session.get(IngestionRunRecord, run_id)
            return _record_to_ingestion_run(record) if record else None

    def save_ingestion_artifact(self, artifact: RawIngestionArtifact) -> RawIngestionArtifact:
        with self._session_factory() as session:
            session.add(_ingestion_artifact_to_record(artifact))
            session.commit()
        return artifact

    def get_ingestion_artifact(self, artifact_id: str) -> RawIngestionArtifact | None:
        with self._session_factory() as session:
            record = session.get(RawIngestionArtifactRecord, artifact_id)
            return _record_to_ingestion_artifact(record) if record else None

    def list_ingestion_artifacts(self, run_id: str) -> list[RawIngestionArtifact]:
        with self._session_factory() as session:
            records = session.scalars(
                select(RawIngestionArtifactRecord)
                .where(RawIngestionArtifactRecord.run_id == run_id)
                .order_by(RawIngestionArtifactRecord.artifact_id)
            ).all()
            return [_record_to_ingestion_artifact(record) for record in records]

    def save_docs_fetch_cache(self, entry: DocsFetchCacheEntry) -> DocsFetchCacheEntry:
        with self._session_factory() as session:
            existing = session.get(DocsFetchCacheRecord, entry.url)
            if existing is None:
                session.add(_docs_cache_to_record(entry))
            else:
                existing.etag = entry.etag
                existing.last_modified = entry.last_modified
                existing.body_hash = entry.body_hash
                existing.last_fetched_at = entry.last_fetched_at
                existing.last_artifact_id = entry.last_artifact_id
            session.commit()
        return entry

    def get_docs_fetch_cache(self, url: str) -> DocsFetchCacheEntry | None:
        with self._session_factory() as session:
            record = session.get(DocsFetchCacheRecord, url)
            return _record_to_docs_cache(record) if record else None

    # -------- Stage 13: human-review + audit accessors --------

    def save_human_review(
        self,
        review: HumanReview,
    ) -> HumanReview:
        """Persist a `HumanReview` and emit its audit event in one txn.

        Append-only: this method INSERTs a new row keyed on
        `review_id`. There is no equivalent `update_human_review` —
        corrections happen by filing a new review with notes that
        reference the prior `review_id`.
        """
        with self._session_factory() as session:
            if session.get(KnowledgeClaimRecord, review.claim_id) is None:
                raise ValueError(
                    f"Claim '{review.claim_id}' does not exist; cannot file a review against it."
                )
            if session.get(HumanReviewRecord, review.review_id) is not None:
                raise DuplicateHumanReviewError(
                    f"Human review '{review.review_id}' already exists."
                )
            session.add(_human_review_to_record(review))
            _add_audit_event(
                session,
                event_type=AuditEventType.HUMAN_REVIEW_RECORDED,
                entity_type=AuditEntityType.CLAIM,
                entity_id=review.claim_id,
                actor=review.reviewer_id,
                details={
                    "review_id": review.review_id,
                    "decision": review.decision.value,
                    "notes_length": len(review.notes),
                },
            )
            session.commit()
        return review

    def record_query_served(
        self,
        *,
        query_id: str,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        """Stage 18 — append one ``QUERY_SERVED`` audit event per ``ask()``
        call. Used by ``QueryEngine.ask`` so the cost-savings aggregator at
        ``GET /v1/stats/savings`` can replay the full query stream from
        the audit log without recording raw question text.

        ``query_id`` is the synthetic entity id minted by the caller
        (``app.services.ids.generate_query_id``). ``actor`` is typically
        the API caller (``"agent"`` if anonymous) or the audited identity
        when ``AYIRU_API_KEY`` is enforced. ``details`` is JSON-encoded
        verbatim; expected keys are ``question_length``, ``answers_returned``,
        ``tokens_saved``, ``top_claim_id``, ``fallback_recommended``,
        ``request_id``.

        Same append-only contract as every other audit emission: the
        helper opens its own transaction and commits, so a failure here
        cannot roll back the upstream ``ask`` response. We do not raise
        on failure — telemetry must never break a working read endpoint.
        """
        try:
            with self._session_factory() as session:
                _add_audit_event(
                    session,
                    event_type=AuditEventType.QUERY_SERVED,
                    entity_type=AuditEntityType.QUERY,
                    entity_id=query_id,
                    actor=actor,
                    details=details,
                )
                session.commit()
        except Exception:
            # Telemetry must never break the read path. Swallow the
            # error; the operator will notice via the structured-log
            # WARN below.
            import logging

            logging.getLogger("ayiru.telemetry").warning(
                "QUERY_SERVED audit emission failed",
                exc_info=True,
                extra={"query_id": query_id},
            )

    def list_human_reviews_for_claim(self, claim_id: str) -> list[HumanReview]:
        with self._session_factory() as session:
            records = session.scalars(
                select(HumanReviewRecord)
                .where(HumanReviewRecord.claim_id == claim_id)
                .order_by(
                    HumanReviewRecord.reviewed_at,
                    HumanReviewRecord.review_id,
                )
            ).all()
            return [_record_to_human_review(record) for record in records]

    def list_pending_review_claims(
        self,
        *,
        tool_id: str | None = None,
        risk_level: RiskLevel | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ReviewQueueItem], int]:
        """Return claims awaiting a human decision plus a total count.

        A claim is "pending review" when its current `verification_status`
        is `REQUIRES_HUMAN_REVIEW`. Ordering is `(created_at ASC,
        claim_id ASC)` so reviewers triage the oldest claims first.
        """
        from sqlalchemy import func

        base = select(KnowledgeClaimRecord).where(
            KnowledgeClaimRecord.verification_status
            == VerificationStatus.REQUIRES_HUMAN_REVIEW.value
        )
        if tool_id is not None:
            base = base.where(KnowledgeClaimRecord.tool_id == tool_id)
        if risk_level is not None:
            base = base.where(KnowledgeClaimRecord.risk_level == risk_level.value)

        statement = base.order_by(
            KnowledgeClaimRecord.created_at,
            KnowledgeClaimRecord.claim_id,
        ).limit(limit).offset(offset)

        count_statement = select(func.count()).select_from(base.subquery())

        with self._session_factory() as session:
            records = list(session.scalars(statement).all())
            total = session.scalar(count_statement) or 0
            claim_ids = [record.claim_id for record in records]
            latest_results = self.get_latest_verification_results(claim_ids)

        items = [
            _claim_record_to_queue_item(
                record,
                latest=latest_results.get(record.claim_id),
            )
            for record in records
        ]
        return items, int(total)

    def list_audit_events(
        self,
        *,
        entity_type: AuditEntityType | None = None,
        entity_id: str | None = None,
        event_type: AuditEventType | None = None,
        actor: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        from sqlalchemy import func

        base = select(AuditEventRecord)
        if entity_type is not None:
            base = base.where(AuditEventRecord.entity_type == entity_type.value)
        if entity_id is not None:
            base = base.where(AuditEventRecord.entity_id == entity_id)
        if event_type is not None:
            base = base.where(AuditEventRecord.event_type == event_type.value)
        if actor is not None:
            base = base.where(AuditEventRecord.actor == actor)
        if after is not None:
            base = base.where(AuditEventRecord.created_at >= after)
        if before is not None:
            base = base.where(AuditEventRecord.created_at < before)

        statement = base.order_by(
            AuditEventRecord.created_at,
            AuditEventRecord.event_id,
        ).limit(limit).offset(offset)

        count_statement = select(func.count()).select_from(base.subquery())

        with self._session_factory() as session:
            records = session.scalars(statement).all()
            total = session.scalar(count_statement) or 0
            return [_record_to_audit_event(record) for record in records], int(total)

    def get_audit_events_for_claim(self, claim_id: str) -> list[AuditEvent]:
        """Chronological audit trail for a single claim — convenience over
        `list_audit_events` that returns every event without pagination so
        history endpoints can render the full timeline in one call.

        The hard cap here mirrors the matcher's safety valve (10k); a
        single claim hitting that limit means something operationally
        weird is happening and the caller should investigate."""
        events, _ = self.list_audit_events(
            entity_type=AuditEntityType.CLAIM,
            entity_id=claim_id,
            limit=10_000,
            offset=0,
        )
        return events

    def aggregate_query_savings(
        self,
        *,
        after: datetime | None = None,
        before: datetime | None = None,
    ) -> dict[str, Any]:
        """Stage 18.4 — return the cost-savings aggregate over QUERY_SERVED
        events in the given window.

        Returns a dict shaped for direct serialisation into the
        ``SavingsResponse`` schema:

        - ``total_queries_served``: int — count of QUERY_SERVED rows
        - ``total_tokens_saved``: int — sum of details.tokens_saved
        - ``by_top_claim``: dict[claim_id, int] — count of times each
          claim was the top match. Fallbacks (``top_claim_id is None``)
          are bucketed under the literal key ``"__fallback__"`` so the
          caller can quantify miss rate.
        - ``fallback_count``: int — count of fallback events in the window.
        - ``window_start`` / ``window_end``: passed through from the
          caller's filter (None if unbounded).

        The aggregation runs in Python over a paginated read of the
        underlying rows. At v0.2 scale (≤ 100k events) that's fine;
        a future SQL-side aggregation can land if telemetry volume
        outgrows the in-memory window.
        """
        events, total = self.list_audit_events(
            event_type=AuditEventType.QUERY_SERVED,
            after=after,
            before=before,
            limit=10_000,
            offset=0,
        )

        total_tokens_saved = 0
        fallback_count = 0
        by_top_claim: dict[str, int] = {}
        for event in events:
            details = event.details
            tokens = details.get("tokens_saved", 0) or 0
            total_tokens_saved += int(tokens)
            top = details.get("top_claim_id")
            if top is None or details.get("fallback_recommended"):
                fallback_count += 1
                by_top_claim["__fallback__"] = by_top_claim.get("__fallback__", 0) + 1
            else:
                by_top_claim[top] = by_top_claim.get(top, 0) + 1

        return {
            "total_queries_served": int(total),
            "total_tokens_saved": total_tokens_saved,
            "fallback_count": fallback_count,
            "by_top_claim": by_top_claim,
            "window_start": after,
            "window_end": before,
        }

    def clear(self) -> None:
        with self._session_factory() as session:
            session.execute(delete(AuditEventRecord))
            session.execute(delete(HumanReviewRecord))
            session.execute(delete(RawIngestionArtifactRecord))
            session.execute(delete(IngestionRunRecord))
            session.execute(delete(CanonicalWorkflowSpecRecord))
            session.execute(delete(CanonicalToolSpecRecord))
            session.execute(delete(VerificationResultRecord))
            session.execute(delete(EvidenceRecord))
            session.execute(delete(KnowledgeClaimRecord))
            session.commit()


def _maybe_filter(
    statement: Select[tuple[KnowledgeClaimRecord]],
    value: str | None,
    predicate: Callable[[str], object],
) -> Select[tuple[KnowledgeClaimRecord]]:
    if value is None:
        return statement
    return statement.where(predicate(value))


def _normalize_semantic_subject(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _claim_to_record(claim: KnowledgeClaim) -> KnowledgeClaimRecord:
    return KnowledgeClaimRecord(
        claim_id=claim.claim_id,
        claim_type=claim.claim_type.value,
        subject=claim.subject,
        statement=claim.statement,
        tool_id=claim.tool_id,
        submitted_by=claim.submitted_by,
        risk_level=claim.risk_level.value,
        risk_level_classified=claim.risk_level_classified.value if claim.risk_level_classified else None,
        risk_assessment=claim.risk_assessment.model_dump_json() if claim.risk_assessment else None,
        verification_status=claim.verification_status.value,
        confidence=claim.confidence,
        confidence_band=claim.confidence_band.value if claim.confidence_band else None,
        created_at=claim.created_at,
        evidence=[
            EvidenceRecord(
                evidence_id=evidence.evidence_id,
                evidence_type=evidence.evidence_type.value,
                source_uri=evidence.source_uri,
                excerpt=evidence.excerpt,
                hash=evidence.hash,
                captured_at=evidence.captured_at,
                trust_level=evidence.trust_level.value,
            )
            for evidence in claim.evidence
        ],
    )


def _record_to_claim(record: KnowledgeClaimRecord) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=record.claim_id,
        claim_type=record.claim_type,
        subject=record.subject,
        statement=record.statement,
        tool_id=record.tool_id,
        submitted_by=record.submitted_by,
        evidence=[_record_to_evidence(evidence) for evidence in record.evidence],
        risk_level=record.risk_level,
        risk_level_classified=record.risk_level_classified,
        risk_assessment=(
            RiskAssessment.model_validate_json(record.risk_assessment)
            if record.risk_assessment
            else None
        ),
        verification_status=record.verification_status,
        confidence=record.confidence,
        confidence_band=ConfidenceBand(record.confidence_band) if record.confidence_band else None,
        created_at=_ensure_utc(record.created_at),
    )


def _record_to_evidence(record: EvidenceRecord) -> Evidence:
    return Evidence(
        evidence_id=record.evidence_id,
        evidence_type=record.evidence_type,
        source_uri=record.source_uri,
        excerpt=record.excerpt,
        hash=record.hash,
        captured_at=_ensure_utc(record.captured_at),
        trust_level=record.trust_level,
    )


def _verification_result_to_record(result: VerificationResult) -> VerificationResultRecord:
    return VerificationResultRecord(
        verification_id=result.verification_id,
        claim_id=result.claim_id,
        decision=result.decision.value,
        verification_status=result.verification_status.value,
        verification_level=result.verification_level.value,
        confidence=result.confidence,
        confidence_band=result.confidence_band.value,
        confidence_breakdown=result.confidence_breakdown.model_dump_json(),
        risk_assessment=result.risk_assessment.model_dump_json(),
        reason_codes=json.dumps(result.reason_codes),
        reasons=json.dumps(result.reasons),
        verified_at=result.verified_at,
    )


def _record_to_verification_result(record: VerificationResultRecord) -> VerificationResult:
    return VerificationResult(
        verification_id=record.verification_id,
        claim_id=record.claim_id,
        decision=record.decision,
        verification_status=record.verification_status,
        verification_level=record.verification_level,
        confidence=record.confidence,
        confidence_band=ConfidenceBand(record.confidence_band),
        confidence_breakdown=ConfidenceBreakdown.model_validate_json(record.confidence_breakdown),
        risk_assessment=RiskAssessment.model_validate_json(record.risk_assessment),
        reason_codes=json.loads(record.reason_codes),
        reasons=json.loads(record.reasons),
        verified_at=_ensure_utc(record.verified_at),
    )


def _tool_spec_to_record(spec: ToolSpec) -> CanonicalToolSpecRecord:
    spec_json = canonical_spec_json(spec)
    return CanonicalToolSpecRecord(
        tool_id=spec.tool_id,
        spec_json=spec_json,
        spec_hash=canonical_spec_hash(spec),
        content_hash=canonical_content_hash(spec),
        compiled_at=spec.provenance.compiled_at,
        compiled_by=spec.provenance.compiled_by,
        source_claim_ids_json=json.dumps(spec.provenance.source_claim_ids),
        verification_level=spec.verification_level.value,
    )


def _record_to_tool_spec(record: CanonicalToolSpecRecord) -> ToolSpec:
    return ToolSpec.model_validate_json(record.spec_json)


def _workflow_spec_to_record(spec: WorkflowSpec) -> CanonicalWorkflowSpecRecord:
    spec_json = canonical_spec_json(spec)
    return CanonicalWorkflowSpecRecord(
        workflow_id=spec.workflow_id,
        spec_json=spec_json,
        spec_hash=canonical_spec_hash(spec),
        content_hash=canonical_content_hash(spec),
        compiled_at=spec.provenance.compiled_at,
        compiled_by=spec.provenance.compiled_by,
        source_claim_ids_json=json.dumps(spec.provenance.source_claim_ids),
        verification_level=spec.verification_level.value,
    )


def _record_to_workflow_spec(record: CanonicalWorkflowSpecRecord) -> WorkflowSpec:
    return WorkflowSpec.model_validate_json(record.spec_json)


def _ingestion_run_to_record(run: IngestionRun) -> IngestionRunRecord:
    return IngestionRunRecord(
        run_id=run.run_id,
        tool_id=run.tool_id,
        command=run.command,
        status=run.status.value,
        submitted_by=run.submitted_by,
        created_at=run.created_at,
        completed_at=run.completed_at,
        summary_json=run.summary.model_dump_json(),
    )


def _record_to_ingestion_run(record: IngestionRunRecord) -> IngestionRun:
    return IngestionRun(
        run_id=record.run_id,
        tool_id=record.tool_id,
        command=record.command,
        status=IngestionStatus(record.status),
        submitted_by=record.submitted_by,
        created_at=_ensure_utc(record.created_at),
        completed_at=_ensure_utc(record.completed_at) if record.completed_at else None,
        summary=CliIngestionSummary.model_validate_json(record.summary_json),
    )


def _ingestion_artifact_to_record(
    artifact: RawIngestionArtifact,
) -> RawIngestionArtifactRecord:
    return RawIngestionArtifactRecord(
        artifact_id=artifact.artifact_id,
        run_id=artifact.run_id,
        artifact_type=artifact.artifact_type.value,
        source_uri=artifact.source_uri,
        raw_content=artifact.raw_content,
        hash=artifact.hash,
        captured_at=artifact.captured_at,
    )


def _record_to_ingestion_artifact(
    record: RawIngestionArtifactRecord,
) -> RawIngestionArtifact:
    return RawIngestionArtifact(
        artifact_id=record.artifact_id,
        run_id=record.run_id,
        artifact_type=IngestionArtifactType(record.artifact_type),
        source_uri=record.source_uri,
        raw_content=record.raw_content,
        hash=record.hash,
        captured_at=_ensure_utc(record.captured_at),
    )


def _docs_cache_to_record(entry: DocsFetchCacheEntry) -> DocsFetchCacheRecord:
    return DocsFetchCacheRecord(
        url=entry.url,
        etag=entry.etag,
        last_modified=entry.last_modified,
        body_hash=entry.body_hash,
        last_fetched_at=entry.last_fetched_at,
        last_artifact_id=entry.last_artifact_id,
    )


def _record_to_docs_cache(record: DocsFetchCacheRecord) -> DocsFetchCacheEntry:
    return DocsFetchCacheEntry(
        url=record.url,
        etag=record.etag,
        last_modified=record.last_modified,
        body_hash=record.body_hash,
        last_fetched_at=_ensure_utc(record.last_fetched_at),
        last_artifact_id=record.last_artifact_id,
    )


def canonical_spec_json(spec: ToolSpec | WorkflowSpec) -> str:
    payload: dict[str, Any] = spec.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def canonical_spec_hash(spec: ToolSpec | WorkflowSpec) -> str:
    return f"sha256:{sha256(canonical_spec_json(spec).encode()).hexdigest()}"


def canonical_content_json(spec: ToolSpec | WorkflowSpec) -> str:
    payload: dict[str, Any] = spec.model_dump(mode="json")
    payload.pop("content_hash", None)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("compiled_at", None)
        provenance.pop("compiled_by", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def canonical_content_hash(spec: ToolSpec | WorkflowSpec) -> str:
    return f"sha256:{sha256(canonical_content_json(spec).encode()).hexdigest()}"


def _add_audit_event(
    session: Session,
    *,
    event_type: AuditEventType,
    entity_type: AuditEntityType,
    entity_id: str,
    actor: str,
    details: dict[str, Any],
) -> None:
    """Stage 13: stage one append-only audit row inside the given session.

    The session is committed by the caller; this helper only stages an
    INSERT. Keeping it inline means the audit row lives or dies with the
    state change that produced it — there is no path that commits one
    without the other.

    `event_id` is generated here so callers don't have to. Timestamps are
    UTC-aware. `details` is JSON-encoded; an empty dict is fine.
    """
    from app.services.ids import generate_audit_event_id

    record = AuditEventRecord(
        event_id=generate_audit_event_id(),
        event_type=event_type.value,
        entity_type=entity_type.value,
        entity_id=entity_id,
        actor=actor,
        details_json=json.dumps(details, ensure_ascii=False, sort_keys=True),
        created_at=datetime.now(timezone.utc),
    )
    session.add(record)


def _human_review_to_record(review: HumanReview) -> HumanReviewRecord:
    return HumanReviewRecord(
        review_id=review.review_id,
        claim_id=review.claim_id,
        reviewer_id=review.reviewer_id,
        decision=review.decision.value,
        notes=review.notes,
        reviewed_at=review.reviewed_at,
    )


def _record_to_human_review(record: HumanReviewRecord) -> HumanReview:
    return HumanReview(
        review_id=record.review_id,
        claim_id=record.claim_id,
        reviewer_id=record.reviewer_id,
        decision=HumanReviewDecision(record.decision),
        notes=record.notes,
        reviewed_at=_ensure_utc(record.reviewed_at),
    )


def _record_to_audit_event(record: AuditEventRecord) -> AuditEvent:
    try:
        details = json.loads(record.details_json) if record.details_json else {}
    except json.JSONDecodeError:
        # Defensive: row was hand-edited or written by an older schema.
        # Surface as an empty details dict rather than crashing the
        # entire history endpoint.
        details = {}
    return AuditEvent(
        event_id=record.event_id,
        event_type=AuditEventType(record.event_type),
        entity_type=AuditEntityType(record.entity_type),
        entity_id=record.entity_id,
        actor=record.actor,
        details=details,
        created_at=_ensure_utc(record.created_at),
    )


def _claim_record_to_queue_item(
    record: KnowledgeClaimRecord,
    *,
    latest: VerificationResult | None,
) -> ReviewQueueItem:
    if latest is not None:
        verification_level = latest.verification_level
        reasons = list(latest.reasons)
    else:
        # No verification result yet — the claim was submitted but the
        # orchestrator hasn't run. This should be vanishingly rare but
        # the queue should still surface the row.
        verification_level = VerificationLevel.L0_UNVERIFIED
        reasons = []
    return ReviewQueueItem(
        claim_id=record.claim_id,
        tool_id=record.tool_id,
        subject=record.subject,
        statement=record.statement,
        risk_level=record.risk_level,
        verification_level=verification_level,
        verification_status=VerificationStatus(record.verification_status),
        confidence=record.confidence if record.confidence is not None else 0.0,
        submitted_by=record.submitted_by,
        created_at=_ensure_utc(record.created_at),
        latest_verification_reasons=reasons,
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


_CLAIM_STORE: ClaimStore | None = None


def get_claim_store() -> ClaimStore:
    global _CLAIM_STORE
    if _CLAIM_STORE is None:
        _CLAIM_STORE = ClaimStore()
    return _CLAIM_STORE
