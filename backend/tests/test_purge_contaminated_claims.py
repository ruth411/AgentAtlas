from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "purge_contaminated_claims",
    ROOT / "tools" / "scripts" / "purge_contaminated_claims.py",
)
purge_module = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(purge_module)


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE knowledge_claims (
            claim_id TEXT PRIMARY KEY,
            tool_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            statement TEXT NOT NULL,
            verification_status TEXT NOT NULL
        );
        CREATE TABLE audit_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO knowledge_claims VALUES (?, ?, ?, ?, ?)",
        [
            ("c1", "helm-cli", "helm", "Skip to content Helm v3.1.0 is out!", "accepted"),
            ("c2", "git-cli", "git commit", "Record changes to the repository.", "accepted"),
            ("c3", "go-cli", "go", "Why Go arrow_drop_down", "requires_human_review"),
        ],
    )
    conn.commit()
    conn.close()


def test_purge_demotes_only_contaminated_accepted_claims(tmp_path) -> None:
    db = tmp_path / "catalog.db"
    _seed_db(db)
    conn = sqlite3.connect(db)
    try:
        report = purge_module.purge(conn, dry_run=False)
    finally:
        conn.close()

    assert report["demoted"] == 1  # only the accepted chrome claim
    assert report["by_family"] == {"helm": 1}

    conn = sqlite3.connect(db)
    try:
        status = dict(
            conn.execute("SELECT claim_id, verification_status FROM knowledge_claims").fetchall()
        )
        # The chrome claim is demoted; the clean claim stays accepted; the
        # already-demoted claim is untouched.
        assert status == {
            "c1": "requires_human_review",
            "c2": "accepted",
            "c3": "requires_human_review",
        }
        events = conn.execute(
            "SELECT entity_id, event_type FROM audit_events"
        ).fetchall()
        assert events == [("c1", "verification_recorded")]
    finally:
        conn.close()


def test_purge_dry_run_writes_nothing(tmp_path) -> None:
    db = tmp_path / "catalog.db"
    _seed_db(db)
    conn = sqlite3.connect(db)
    try:
        report = purge_module.purge(conn, dry_run=True)
    finally:
        conn.close()

    assert report["demoted"] == 1
    assert report["applied"] is False

    conn = sqlite3.connect(db)
    try:
        accepted = conn.execute(
            "SELECT COUNT(*) FROM knowledge_claims WHERE verification_status='accepted'"
        ).fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        assert accepted == 2  # unchanged
        assert events == 0
    finally:
        conn.close()
