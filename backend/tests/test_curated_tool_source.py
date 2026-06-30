from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import sys

from app.services.curated_tool_source import (
    curated_tool_source_validator,
    ingest_curated_tool_source,
    load_curated_tool_source,
)
from app.services.structured_knowledge_store import StructuredKnowledgeStore


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


prose_module = _load_module(
    "structured_ingest_prose_depth_for_curated",
    "tools/scripts/structured_ingest_prose_depth.py",
)
migrate_module = _load_module(
    "migrate_seed_to_curated_source",
    "tools/scripts/migrate_seed_to_curated_source.py",
)


def test_migrate_seed_builds_valid_curated_document(tmp_path: Path) -> None:
    seed = tmp_path / "seed_demo.py"
    seed.write_text(
        """
BASE = "https://example.com"
CLAIMS = [
    ("ssh-errors", "ssh error: demo", "Fix: (1) inspect with `ssh -v user@host`; (2) retry.", f"{BASE}/ssh.1"),
    ("ssh-recipes", "ssh recipe: tunnel", "Run `ssh -L 8080:host:80 user@host`; verify with `curl localhost:8080`.", f"{BASE}/ssh.1"),
]
"""
    )

    fake_cfg = prose_module.ToolSeedConfig("ssh", seed, "ssh")
    fake_source_module = SimpleNamespace(
        TOOLS={"demo": fake_cfg},
        load_seed_claims=prose_module.load_seed_claims,
        _kind_for_claim=prose_module._kind_for_claim,
        _normalize_title=prose_module._normalize_title,
        _subject_id=prose_module._subject_id,
        _extract_commands=prose_module._extract_commands,
        _extract_steps=prose_module._extract_steps,
        _extract_preconditions=prose_module._extract_preconditions,
        _effect_profile=prose_module._effect_profile,
    )

    document = migrate_module.build_document("demo", fake_source_module)

    curated_tool_source_validator().validate(document)
    assert document["family"] == "ssh"
    assert document["artifact_id"] == "ssh-curated-source-v1"
    assert [entry["kind"] for entry in document["entries"]] == ["error", "recipe"]
    assert document["entries"][1]["commands"] == [
        "ssh -L 8080:host:80 user@host",
        "curl localhost:8080",
    ]


def test_ingest_curated_source_persists_provenance_and_rows(tmp_path: Path) -> None:
    document = {
        "family": "ssh",
        "version": "v1",
        "artifact_id": "ssh-curated-source-v1",
        "generated_from": {
            "kind": "legacy_seed_script",
            "path": "tools/scripts/seed_ssh_errors_recipes.py",
        },
        "entries": [
            {
                "entry_id": "ssh-recipe-local-port-forward",
                "kind": "recipe",
                "title": "ssh recipe: local port forward",
                "statement": "Run a local forward and verify it locally.",
                "source_url": "https://man.openbsd.org/ssh.1",
                "source_type": "official_docs",
                "commands": [
                    "ssh -L 8080:host:80 user@host",
                    "curl localhost:8080",
                ],
                "steps": [
                    "Run the local port forward.",
                    "Verify the forwarded port locally.",
                ],
                "constraints": [
                    "Requires network reachability to the SSH server.",
                ],
                "effect": {
                    "kind": "network",
                    "destructive": False,
                    "reversible": True,
                    "mutates_remote_state": False,
                    "may_cost_money": False,
                    "may_expose_secrets": False,
                    "risk_level": "low",
                    "reasons": ["Uses an SSH tunnel without remote mutation."],
                },
                "provenance": {
                    "source_artifact_id": "ssh-curated-source-v1",
                    "migration_method": "legacy_seed_projection_v1",
                    "legacy_seed_script": "tools/scripts/seed_ssh_errors_recipes.py",
                    "legacy_tool_id": "ssh-recipes",
                    "legacy_title": "ssh recipe: local port forward",
                },
                "confidence": 0.92,
            }
        ],
    }
    db_path = tmp_path / "curated-source.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")

    report = ingest_curated_tool_source(store, document, now=FIXED_TIME)

    assert report == {
        "subjects": 1,
        "capabilities": 4,
        "constraints": 2,
        "effects": 1,
    }

    conn = sqlite3.connect(db_path)
    try:
        subject = conn.execute(
            "SELECT subject_kind, provenance_claim_ids_json FROM subjects WHERE subject_id = 'ssh-recipe-local-port-forward'"
        ).fetchone()
        assert subject == ("workflow", '["ssh-recipe-local-port-forward"]')

        capability_json = conn.execute(
            "SELECT detail_json FROM capabilities WHERE subject_id = 'ssh-recipe-local-port-forward' AND capability_type = 'workflow'"
        ).fetchone()
        assert capability_json is not None
        capability = json.loads(capability_json[0])
        assert capability["provenance"]["import_method"] == "curated_tool_source"
        assert capability["provenance"]["entry_id"] == "ssh-recipe-local-port-forward"

        effect = conn.execute(
            "SELECT effect_kind, destructive, mutates_remote_state, detail_json FROM effects WHERE subject_id = 'ssh-recipe-local-port-forward'"
        ).fetchone()
        assert effect is not None
        assert effect[0:3] == ("network", 0, 0)
        assert json.loads(effect[3])["provenance"]["source_artifact_id"] == "ssh-curated-source-v1"
    finally:
        conn.close()


def test_checked_in_curated_source_files_validate() -> None:
    files = sorted((ROOT / "tools" / "tool_sources").glob("*.v1.json"))
    assert files, "Expected at least one checked-in curated source file."
    for path in files:
        data = load_curated_tool_source(path)
        curated_tool_source_validator().validate(data)
