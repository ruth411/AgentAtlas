from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys

from app.schemas.enums import ConfidenceBand, RiskLevel, VerificationLevel, VerificationStatus
from app.services.structured_knowledge_store import (
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)


ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = datetime(2026, 6, 30, tzinfo=timezone.utc)


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


module = _load_module("report_tool_coverage", "tools/scripts/report_tool_coverage.py")


def test_collect_family_coverage_scores_families_with_breakdown(tmp_path: Path) -> None:
    db_path = tmp_path / "coverage.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")

    store.upsert_subject_graph(
        StructuredSubject(
            subject_id="ssh-recipe-tunnel",
            subject_kind="workflow",
            name="ssh recipe: tunnel",
            family="ssh",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            created_at=FIXED_TIME,
            updated_at=FIXED_TIME,
        ),
        capabilities=[
            StructuredCapability(
                capability_id="cap-ssh-invoke",
                subject_id="ssh-recipe-tunnel",
                capability_type="invocation",
                title="ssh tunnel invocation",
                detail={
                    "kind": "invocation",
                    "command": "ssh",
                    "source_url": "https://man.openbsd.org/ssh.1",
                    "usage_signature": "ssh -L 8080:host:80 user@host",
                    "argv_schema": {"program": "ssh", "subcommand_path": [], "positionals": []},
                    "flag_schema": [],
                    "synopsis": "ssh -L 8080:host:80 user@host",
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.99,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.LOW,
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
            )
        ],
        constraints=[
            StructuredConstraint(
                constraint_id="constraint-ssh-env",
                subject_id="ssh-recipe-tunnel",
                constraint_kind="environment",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                detail={"source_url": "https://man.openbsd.org/ssh.1"},
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
            )
        ],
        effects=[
            StructuredEffect(
                effect_id="effect-ssh-network",
                subject_id="ssh-recipe-tunnel",
                effect_kind="network",
                destructive=False,
                reversible=True,
                mutates_remote_state=False,
                may_cost_money=False,
                    may_expose_secrets=False,
                    verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                    detail={"source_url": "https://man.openbsd.org/ssh.1"},
                    created_at=FIXED_TIME,
                    updated_at=FIXED_TIME,
                )
            ],
        )

    store.upsert_subject_graph(
        StructuredSubject(
            subject_id="vim-help",
            subject_kind="subject",
            name="vim left-right motions",
            family="vim",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            created_at=FIXED_TIME,
            updated_at=FIXED_TIME,
        ),
        capabilities=[
            StructuredCapability(
                capability_id="cap-vim-meta",
                subject_id="vim-help",
                capability_type="metadata",
                title="vim h moves left",
                detail={
                    "kind": "metadata",
                    "command": "vim",
                    "source_url": "file:///usr/share/vim/vim91/doc/quickref.txt",
                    "aliases": [],
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=0.93,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.NONE,
                created_at=FIXED_TIME,
                updated_at=FIXED_TIME,
            )
        ],
        constraints=[],
        effects=[],
    )

    rows = module.collect_family_coverage(db_path)
    by_family = {row.family: row for row in rows}

    assert {"ssh", "vim"} <= set(by_family)
    assert by_family["ssh"].breakdown.total > by_family["vim"].breakdown.total
    assert by_family["ssh"].subjects_with_invocation == 1
    assert by_family["ssh"].provenance_complete_rows == by_family["ssh"].provenance_total_rows
