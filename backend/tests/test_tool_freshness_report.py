from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys

from app.schemas.enums import ConfidenceBand, RiskLevel, VerificationLevel, VerificationStatus
from app.services.structured_knowledge_store import (
    StructuredCapability,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 6, 30, tzinfo=timezone.utc)


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


module = _load_module("report_catalog_freshness", "tools/scripts/report_catalog_freshness.py")


def test_collect_family_freshness_counts_stale_and_missing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "freshness.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")

    store.upsert_subject_graph(
        StructuredSubject(
            subject_id="ssh-recipe-tunnel",
            subject_kind="workflow",
            name="ssh recipe: tunnel",
            family="ssh",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            created_at=NOW,
            updated_at=NOW,
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
                    "provenance": {"imported_at": "2026-06-25T00:00:00+00:00"},
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.99,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.LOW,
                created_at=NOW,
                updated_at=NOW,
            )
        ],
        constraints=[],
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
                detail={
                    "source_url": "https://man.openbsd.org/ssh.1",
                    "provenance": {"imported_at": "2026-06-25T00:00:00+00:00"},
                },
                created_at=NOW,
                updated_at=NOW,
            )
        ],
    )

    store.upsert_subject_graph(
        StructuredSubject(
            subject_id="vim-help",
            subject_kind="subject",
            name="vim help",
            family="vim",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            created_at=NOW,
            updated_at=NOW,
        ),
        capabilities=[
            StructuredCapability(
                capability_id="cap-vim-meta",
                subject_id="vim-help",
                capability_type="metadata",
                title="vim help",
                detail={
                    "kind": "metadata",
                    "command": "vim",
                    "source_url": "file:///usr/share/vim/doc/help.txt",
                    "aliases": [],
                    "provenance": {"imported_at": "2026-01-01T00:00:00+00:00"},
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=0.91,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.NONE,
                created_at=NOW,
                updated_at=NOW,
            ),
            StructuredCapability(
                capability_id="cap-vim-missing",
                subject_id="vim-help",
                capability_type="metadata",
                title="vim help missing provenance",
                detail={
                    "kind": "metadata",
                    "command": "vim",
                    "source_url": "file:///usr/share/vim/doc/help.txt",
                    "aliases": [],
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=0.75,
                confidence_band=ConfidenceBand.MODERATE,
                risk_level=RiskLevel.NONE,
                created_at=NOW,
                updated_at=NOW,
            ),
        ],
        constraints=[],
        effects=[],
    )

    rows = module.collect_family_freshness(db_path, now=NOW)
    by_family = {row.family: row for row in rows}

    assert {"ssh", "vim"} <= set(by_family)
    assert by_family["ssh"].stale_30d == 0
    assert by_family["ssh"].missing_timestamp_rows == 0
    assert by_family["vim"].stale_90d == 1
    assert by_family["vim"].stale_180d == 0
    assert by_family["vim"].missing_timestamp_rows == 1
