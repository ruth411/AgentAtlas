from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_slim_catalog",
    ROOT / "tools" / "scripts" / "build_slim_catalog.py",
)
build_module = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
SPEC.loader.exec_module(build_module)


def _create_structured_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE subjects (
            subject_id TEXT PRIMARY KEY,
            subject_kind TEXT NOT NULL,
            name TEXT NOT NULL,
            family TEXT NOT NULL,
            verification_level TEXT NOT NULL,
            provenance_claim_ids_json TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE capabilities (
            capability_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            capability_type TEXT NOT NULL,
            title TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            verification_level TEXT NOT NULL,
            confidence REAL NOT NULL,
            confidence_band TEXT NOT NULL,
            risk_level TEXT,
            provenance_claim_ids_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE constraints (
            constraint_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            constraint_kind TEXT NOT NULL,
            verification_level TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE effects (
            effect_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            effect_kind TEXT NOT NULL,
            destructive INTEGER NOT NULL,
            reversible INTEGER NOT NULL,
            mutates_remote_state INTEGER NOT NULL,
            may_cost_money INTEGER NOT NULL,
            may_expose_secrets INTEGER NOT NULL,
            verification_level TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO subjects
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("gh-pr-create", "tool", "gh pr create", "gh", "L3_runtime_verified", "[]", None, None),
    )
    conn.execute(
        """
        INSERT INTO capabilities
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cap-1",
            "gh-pr-create",
            "invocation",
            "gh pr create invocation",
            '{"command":"gh pr create"}',
            "accepted",
            "L3_runtime_verified",
            0.99,
            "strong",
            "low",
            "[]",
            "structured_ingestion",
            None,
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO constraints
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "con-1",
            "gh-pr-create",
            "environment",
            "L3_runtime_verified",
            '{"requires_binary":"gh"}',
            "structured_ingestion",
            None,
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO effects
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "eff-1",
            "gh-pr-create",
            "network",
            0,
            1,
            1,
            0,
            0,
            "L2_source_verified",
            '{"command":"gh pr create"}',
            "structured_ingestion",
            None,
            None,
        ),
    )
    conn.commit()
    conn.close()


def _create_structured_target(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE subjects (
            subject_id TEXT PRIMARY KEY,
            subject_kind TEXT NOT NULL,
            name TEXT NOT NULL,
            family TEXT NOT NULL,
            verification_level TEXT NOT NULL,
            provenance_claim_ids_json TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE capabilities (
            capability_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            capability_type TEXT NOT NULL,
            title TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            verification_level TEXT NOT NULL,
            confidence REAL NOT NULL,
            confidence_band TEXT NOT NULL,
            risk_level TEXT,
            provenance_claim_ids_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE constraints (
            constraint_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            constraint_kind TEXT NOT NULL,
            verification_level TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE effects (
            effect_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            effect_kind TEXT NOT NULL,
            destructive INTEGER NOT NULL,
            reversible INTEGER NOT NULL,
            mutates_remote_state INTEGER NOT NULL,
            may_cost_money INTEGER NOT NULL,
            may_expose_secrets INTEGER NOT NULL,
            verification_level TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def test_build_resolves_relative_paths_before_alembic_changes_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "nested" / "catalog.db"
    _create_structured_source(source)

    def fake_migrate(target_path: Path) -> None:
        assert target_path.is_absolute()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _create_structured_target(target_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_module, "_migrate_target_schema", fake_migrate)

    counts = build_module.build(
        Path("source.db"),
        Path("nested/catalog.db"),
        ["gh"],
        structured_only=True,
    )

    assert counts == {
        "subjects": 1,
        "capabilities": 1,
        "constraints": 1,
        "effects": 1,
    }
    assert output.is_file()

    conn = sqlite3.connect(output)
    try:
        subject_ids = conn.execute("SELECT subject_id FROM subjects").fetchall()
    finally:
        conn.close()
    assert subject_ids == [("gh-pr-create",)]
