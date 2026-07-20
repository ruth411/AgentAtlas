from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "rebuild_structured_product",
    ROOT / "tools" / "scripts" / "rebuild_structured_product.py",
)
rebuild_module = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
SPEC.loader.exec_module(rebuild_module)


def test_main_prints_new_smoke_fields_without_keyerror(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "structured.db"
    conn = sqlite3.connect(database_path)
    try:
        for table in ("subjects", "capabilities", "constraints", "effects"):
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
            conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        rebuild_module,
        "run_smoke",
        lambda **_kwargs: {
            "bulk_subject_id": "gh-pr-create",
            "bulk_cap": "cap-bulk",
            "bulk_effects": 1,
            "bundle_subject_id": "gh-pr-create",
            "bundle_cap": "cap-bundle",
            "bundle_effects": 2,
            "bundle_action": "cap-bundle-action",
        },
    )
    monkeypatch.setattr(
        rebuild_module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            database=f"sqlite:///{database_path}",
            bundle_output=str(tmp_path / "bundle.db"),
            refresh_curated=False,
            skip_bundle=True,
            skip_smoke=False,
            skip_coverage=True,
            skip_freshness=True,
        ),
    )

    assert rebuild_module.main() == 0
    out = capsys.readouterr().out
    assert "bulk_subject_id=gh-pr-create" in out
    assert "bundle_action=cap-bundle-action" in out
