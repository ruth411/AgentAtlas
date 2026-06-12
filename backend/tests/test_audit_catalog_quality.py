from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "tools" / "scripts" / "audit_catalog_quality.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_catalog_quality", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_claim_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE knowledge_claims (
            tool_id TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            subject TEXT NOT NULL,
            statement TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO knowledge_claims (tool_id, verification_status, subject, statement) "
        "VALUES (?, ?, ?, ?)",
        [
            ("gh-cli", "accepted", "gh repo create", "create a repository"),
            ("gh-errors", "requires_human_review", "gh auth login", "login guidance"),
            ("docker-cli", "accepted", "docker build", "build an image"),
        ],
    )
    conn.commit()


def test_audit_reports_zero_structured_coverage_when_tables_missing(tmp_path) -> None:
    module = _load_module()
    db_path = tmp_path / "audit.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _seed_claim_table(conn)

    report = module.audit_catalog(conn)
    structured = report["structured_coverage"]

    assert structured["baseline_source"] == "knowledge_claims.tool_id"
    assert structured["total_subjects"] == 3
    assert structured["structured_tables"] == {
        "subjects": False,
        "capabilities": False,
        "constraints": False,
        "effects": False,
    }
    assert structured["subjects_with_rows"] == 0
    assert structured["subjects_with_capabilities"] == 0
    assert structured["subjects_with_constraints"] == 0
    assert structured["subjects_with_effects"] == 0

    gh_row = next(row for row in structured["per_family"] if row["family"] == "gh")
    assert gh_row["total_subjects"] == 2
    assert gh_row["subjects_covered"] == 0
    assert gh_row["capabilities_covered"] == 0
    assert report["gh_structured_subcommands"][0] == 0
    assert report["gh_structured_subcommands"][1] > 0


def test_audit_uses_subjects_table_as_structured_baseline(tmp_path) -> None:
    module = _load_module()
    db_path = tmp_path / "audit.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _seed_claim_table(conn)
    conn.execute(
        """
        CREATE TABLE subjects (
            subject_id TEXT PRIMARY KEY,
            family TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE capabilities (subject_id TEXT NOT NULL)")
    conn.execute("CREATE TABLE constraints (subject_id TEXT NOT NULL)")
    conn.execute("CREATE TABLE effects (subject_id TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO subjects (subject_id, family) VALUES (?, ?)",
        [("gh-pr-create", "gh"), ("docker-build", "docker")],
    )
    conn.execute(
        "INSERT INTO capabilities (subject_id) VALUES ('gh-pr-create')"
    )
    conn.execute(
        "INSERT INTO constraints (subject_id) VALUES ('gh-pr-create')"
    )
    conn.execute(
        "INSERT INTO effects (subject_id) VALUES ('docker-build')"
    )
    conn.commit()

    report = module.audit_catalog(conn)
    structured = report["structured_coverage"]

    assert structured["baseline_source"] == "subjects.subject_id"
    assert structured["total_subjects"] == 2
    assert structured["structured_tables"] == {
        "subjects": True,
        "capabilities": True,
        "constraints": True,
        "effects": True,
    }
    assert structured["subjects_with_rows"] == 2
    assert structured["subjects_with_capabilities"] == 1
    assert structured["subjects_with_constraints"] == 1
    assert structured["subjects_with_effects"] == 1

    gh_row = next(row for row in structured["per_family"] if row["family"] == "gh")
    docker_row = next(row for row in structured["per_family"] if row["family"] == "docker")
    assert gh_row["subjects_covered"] == 1
    assert gh_row["capabilities_covered"] == 1
    assert gh_row["constraints_covered"] == 1
    assert gh_row["effects_covered"] == 0
    assert docker_row["effects_covered"] == 1
    assert report["gh_structured_subcommands"][0] == 1
