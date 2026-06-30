from __future__ import annotations

from datetime import datetime, timezone
from functools import cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.schemas.enums import RiskLevel, VerificationLevel, VerificationStatus
from app.services.confidence_scorer import band_for_score
from app.services.contract_paths import contract_path
from app.services.structured_cli_ingestion import _stable_row_id, _validated_detail
from app.services.structured_knowledge_store import (
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)


_FLAG = re.compile(r"(?<!\w)(--[A-Za-z0-9][A-Za-z0-9._-]*|-[A-Za-zA-Z0-9])(?!\w)")
_NON_ID = re.compile(r"[^a-z0-9]+")


class CuratedToolSourceError(ValueError):
    """Raised when curated tool source data is invalid."""


@cache
def curated_tool_source_validator() -> Draft202012Validator:
    schema = json.loads(contract_path("curated_tool_sources.v1.json").read_text())
    return Draft202012Validator(schema)


def load_curated_tool_source(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    try:
        curated_tool_source_validator().validate(data)
    except ValidationError as exc:
        raise CuratedToolSourceError(
            f"Curated tool source failed validation at {path}: {exc.message}"
        ) from exc
    return data


def ingest_curated_tool_source(
    store: StructuredKnowledgeStore,
    document: dict[str, Any],
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, int]:
    captured_at = now or datetime.now(timezone.utc)
    written = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0}
    for entry in document["entries"]:
        bundle = build_curated_subject_bundle(document, entry, captured_at=captured_at)
        written["subjects"] += 1
        written["capabilities"] += len(bundle["capabilities"])
        written["constraints"] += len(bundle["constraints"])
        written["effects"] += len(bundle["effects"])
        if not dry_run:
            store.upsert_subject_graph(
                bundle["subject"],
                capabilities=bundle["capabilities"],
                constraints=bundle["constraints"],
                effects=bundle["effects"],
            )
    return written


def _slug(text: str) -> str:
    text = _NON_ID.sub("-", text.casefold()).strip("-")
    return text[:72] or "item"


def _subject_id(family: str, kind: str, title: str) -> str:
    base = f"{family}-{kind}-{_slug(title)}"
    if len(base) <= 128:
        return base
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    return f"{family}-{kind}-{_slug(title)[:110]}-{digest}"[:128]


def _command_detail(command: str) -> dict[str, Any]:
    flags = sorted(set(_FLAG.findall(command)))
    return {
        "command_example": command,
        "flags": flags,
        "tokens": command.split(),
    }


def build_curated_subject_bundle(
    document: dict[str, Any],
    entry: dict[str, Any],
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    family = document["family"]
    is_workflow = entry["kind"] == "recipe"
    subject_kind = "workflow" if is_workflow else "subject"
    capability_type = "workflow" if is_workflow else "metadata"
    subject_id = entry["entry_id"] or _subject_id(
        family,
        "recipe" if is_workflow else "error",
        entry["title"],
    )
    confidence = float(entry["confidence"])
    effect = entry["effect"]
    risk_level = RiskLevel(effect["risk_level"])
    provenance = {
        **entry["provenance"],
        "artifact_id": document["artifact_id"],
        "source_document_version": document["version"],
        "generated_from": document["generated_from"],
        "entry_id": entry["entry_id"],
        "import_method": "curated_tool_source",
        "imported_at": captured_at.isoformat(),
    }

    subject = StructuredSubject(
        subject_id=subject_id,
        subject_kind=subject_kind,
        name=entry["title"],
        family=family,
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        provenance_claim_ids=[entry["entry_id"]],
        created_at=captured_at,
        updated_at=captured_at,
    )

    commands = list(entry["commands"])
    steps = list(entry["steps"])
    constraints_list = list(entry["constraints"])

    capabilities: list[StructuredCapability] = [
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, capability_type, "summary"),
            subject_id=subject_id,
            capability_type=capability_type,
            title=f"{entry['title']} {'workflow' if is_workflow else 'guidance'}",
            detail={
                "kind": "workflow" if is_workflow else "metadata",
                "command": family,
                "source_url": entry["source_url"],
                "title": entry["title"],
                "summary_text": entry["statement"],
                "steps": steps,
                "commands": commands,
                "provenance": provenance,
            },
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=confidence,
            confidence_band=band_for_score(confidence),
            risk_level=risk_level,
            source="structured_ingestion",
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]

    if commands:
        meta_conf = max(confidence - 0.04, 0.0)
        capabilities.append(
            StructuredCapability(
                capability_id=_stable_row_id(subject_id, "metadata", "examples"),
                subject_id=subject_id,
                capability_type="metadata",
                title=f"{entry['title']} examples",
                detail={
                    "kind": "metadata",
                    "command": family,
                    "source_url": entry["source_url"],
                    "aliases": [],
                    "commands": commands,
                    "step_count": len(steps),
                    "provenance": provenance,
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=meta_conf,
                confidence_band=band_for_score(meta_conf),
                risk_level=RiskLevel.NONE,
                source="structured_ingestion",
                created_at=captured_at,
                updated_at=captured_at,
            )
        )

    for idx, command in enumerate(commands[:6], start=1):
        cmd_conf = max(confidence - 0.06, 0.0)
        capabilities.append(
            StructuredCapability(
                capability_id=_stable_row_id(subject_id, "invocation", f"cmd-{idx}"),
                subject_id=subject_id,
                capability_type="invocation",
                title=f"{entry['title']} example {idx}",
                detail=_validated_detail(
                    {
                        "kind": "invocation",
                        "source_url": entry["source_url"],
                        "usage_signature": command,
                        "command": family,
                        "argv_schema": {
                            "program": family,
                            "subcommand_path": [],
                            "positionals": [],
                        },
                        "flag_schema": [],
                        "synopsis": command,
                    }
                ),
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=cmd_conf,
                confidence_band=band_for_score(cmd_conf),
                risk_level=risk_level,
                source="structured_ingestion",
                created_at=captured_at,
                updated_at=captured_at,
            )
        )

    constraints = [
        StructuredConstraint(
            constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
            subject_id=subject_id,
            constraint_kind="environment",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            detail={
                "source_url": entry["source_url"],
                "requires_binary": family,
                "constraints": constraints_list,
                "provenance": provenance,
            },
            source="structured_ingestion",
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]
    if constraints_list:
        constraints.append(
            StructuredConstraint(
                constraint_id=_stable_row_id(subject_id, "constraint", "preconditions"),
                subject_id=subject_id,
                constraint_kind="precondition",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                detail={
                    "source_url": entry["source_url"],
                    "preconditions": constraints_list,
                    "provenance": provenance,
                },
                source="structured_ingestion",
                created_at=captured_at,
                updated_at=captured_at,
            )
        )

    effects = [
        StructuredEffect(
            effect_id=_stable_row_id(subject_id, "effect", effect["kind"]),
            subject_id=subject_id,
            effect_kind=effect["kind"],
            destructive=bool(effect["destructive"]),
            reversible=bool(effect["reversible"]),
            mutates_remote_state=bool(effect["mutates_remote_state"]),
            may_cost_money=bool(effect["may_cost_money"]),
            may_expose_secrets=bool(effect["may_expose_secrets"]),
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            detail={
                "source_url": entry["source_url"],
                "classification_reason": "; ".join(effect["reasons"]) if effect["reasons"] else "Curated source effect profile.",
                "provenance": provenance,
            },
            source="structured_ingestion",
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]

    return {
        "subject": subject,
        "capabilities": capabilities,
        "constraints": constraints,
        "effects": effects,
    }
