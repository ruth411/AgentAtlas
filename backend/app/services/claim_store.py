from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Select, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.db.models import (
    CanonicalToolSpecRecord,
    CanonicalWorkflowSpecRecord,
    DocsFetchCacheRecord,
    EvidenceRecord,
    IngestionRunRecord,
    KnowledgeClaimRecord,
    RawIngestionArtifactRecord,
    VerificationResultRecord,
)
from app.db.session import SessionLocal, create_database_engine, init_db
from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import (
    ClaimType,
    ConfidenceBand,
    OrchestratorDecision,
    IngestionArtifactType,
    IngestionStatus,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
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
from app.services.evidence_policy import evidence_policy_violations
from app.services.evidence_trust import normalize_claim_evidence_trust


@cache
def _stage_0_tool_ids() -> frozenset[str]:
    """Return the locked Stage 0 tool_id set, loaded once from the contract."""
    path = Path(__file__).resolve().parents[3] / "contracts/agentatlas_stage_0.v1.json"
    with path.open() as handle:
        data = json.load(handle)
    return frozenset(tool["tool_id"] for tool in data["initial_tools"])


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

        # Gate against the locked Stage 0 tool contract so garbage tool_ids
        # cannot quietly accumulate in the store.
        allowed_tools = _stage_0_tool_ids()
        if claim.tool_id not in allowed_tools:
            raise ToolNotAllowedError(claim.tool_id, allowed_tools)

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

    def save_verification_result(self, result: VerificationResult) -> VerificationResult:
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
                else:
                    existing.spec_json = record.spec_json
                    existing.spec_hash = record.spec_hash
                    existing.content_hash = record.content_hash
                    existing.compiled_at = record.compiled_at
                    existing.compiled_by = record.compiled_by
                    existing.source_claim_ids_json = record.source_claim_ids_json
                    existing.verification_level = record.verification_level
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
                else:
                    existing.spec_json = record.spec_json
                    existing.spec_hash = record.spec_hash
                    existing.content_hash = record.content_hash
                    existing.compiled_at = record.compiled_at
                    existing.compiled_by = record.compiled_by
                    existing.source_claim_ids_json = record.source_claim_ids_json
                    existing.verification_level = record.verification_level
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

    def clear(self) -> None:
        with self._session_factory() as session:
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
