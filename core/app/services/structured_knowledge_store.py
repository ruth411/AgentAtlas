from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any

from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    CapabilityRecord,
    ConstraintRecord,
    EffectRecord,
    SubjectRecord,
)
from app.db.session import SessionLocal, create_database_engine, init_db
from app.schemas.enums import ConfidenceBand, RiskLevel, VerificationLevel, VerificationStatus


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class StructuredSubject:
    subject_id: str
    subject_kind: str
    name: str
    family: str
    verification_level: VerificationLevel
    provenance_claim_ids: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class StructuredCapability:
    capability_id: str
    subject_id: str
    capability_type: str
    title: str
    detail: dict[str, Any]
    verification_status: VerificationStatus
    verification_level: VerificationLevel
    confidence: float
    confidence_band: ConfidenceBand
    risk_level: RiskLevel | None
    provenance_claim_ids: list[str] = field(default_factory=list)
    source: str = "structured_ingestion"
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class StructuredConstraint:
    constraint_id: str
    subject_id: str
    constraint_kind: str
    detail: dict[str, Any]
    source: str = "structured_ingestion"
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class StructuredEffect:
    effect_id: str
    subject_id: str
    effect_kind: str
    destructive: bool
    reversible: bool
    mutates_remote_state: bool
    may_cost_money: bool
    may_expose_secrets: bool
    detail: dict[str, Any]
    source: str = "structured_ingestion"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StructuredKnowledgeStore:
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
            self._session_factory = SessionLocal
        else:
            init_db(engine)
            self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def upsert_subject_graph(
        self,
        subject: StructuredSubject,
        *,
        capabilities: list[StructuredCapability],
        constraints: list[StructuredConstraint],
        effects: list[StructuredEffect],
    ) -> None:
        with self._session_factory() as session:
            self._upsert_subject(session, subject)
            session.execute(
                delete(CapabilityRecord).where(CapabilityRecord.subject_id == subject.subject_id)
            )
            session.execute(
                delete(ConstraintRecord).where(ConstraintRecord.subject_id == subject.subject_id)
            )
            session.execute(
                delete(EffectRecord).where(EffectRecord.subject_id == subject.subject_id)
            )
            session.add_all([_capability_to_record(item) for item in capabilities])
            session.add_all([_constraint_to_record(item) for item in constraints])
            session.add_all([_effect_to_record(item) for item in effects])
            session.commit()

    def list_subject_ids(self, *, family: str | None = None) -> list[str]:
        with self._session_factory() as session:
            query = session.query(SubjectRecord.subject_id)
            if family is not None:
                query = query.filter(SubjectRecord.family == family)
            return [row[0] for row in query.order_by(SubjectRecord.subject_id).all()]

    def _upsert_subject(self, session: Session, subject: StructuredSubject) -> None:
        record = session.get(SubjectRecord, subject.subject_id)
        created_at = subject.created_at or subject.updated_at
        updated_at = subject.updated_at or subject.created_at
        if record is None:
            record = SubjectRecord(
                subject_id=subject.subject_id,
                subject_kind=subject.subject_kind,
                name=subject.name,
                family=subject.family,
                verification_level=subject.verification_level.value,
                provenance_claim_ids_json=json.dumps(subject.provenance_claim_ids),
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(record)
            return
        record.subject_kind = subject.subject_kind
        record.name = subject.name
        record.family = subject.family
        record.verification_level = subject.verification_level.value
        record.provenance_claim_ids_json = json.dumps(subject.provenance_claim_ids)
        if created_at is not None and record.created_at is None:
            record.created_at = created_at
        if updated_at is not None:
            record.updated_at = updated_at


def _capability_to_record(item: StructuredCapability) -> CapabilityRecord:
    return CapabilityRecord(
        capability_id=item.capability_id,
        subject_id=item.subject_id,
        capability_type=item.capability_type,
        title=item.title,
        detail_json=_canonical_json(item.detail),
        verification_status=item.verification_status.value,
        verification_level=item.verification_level.value,
        confidence=item.confidence,
        confidence_band=item.confidence_band.value,
        risk_level=item.risk_level.value if item.risk_level else None,
        provenance_claim_ids_json=json.dumps(item.provenance_claim_ids),
        source=item.source,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _constraint_to_record(item: StructuredConstraint) -> ConstraintRecord:
    return ConstraintRecord(
        constraint_id=item.constraint_id,
        subject_id=item.subject_id,
        constraint_kind=item.constraint_kind,
        detail_json=_canonical_json(item.detail),
        source=item.source,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _effect_to_record(item: StructuredEffect) -> EffectRecord:
    return EffectRecord(
        effect_id=item.effect_id,
        subject_id=item.subject_id,
        effect_kind=item.effect_kind,
        destructive=item.destructive,
        reversible=item.reversible,
        mutates_remote_state=item.mutates_remote_state,
        may_cost_money=item.may_cost_money,
        may_expose_secrets=item.may_expose_secrets,
        detail_json=_canonical_json(item.detail),
        source=item.source,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
