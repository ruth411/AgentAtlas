"""Smoke-test the structured Ayiru product surfaces."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.claim_store import ClaimStore  # noqa: E402
from app.services.query_engine import QueryEngine  # noqa: E402


DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"
DEFAULT_CATALOG = REPO_ROOT / "mcp" / "ayiru_mcp" / "data" / "catalog.db"


def _table_count(database_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(database_path))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def run_smoke(
    *,
    database_path: Path,
    catalog_path: Path,
) -> dict[str, object]:
    if not database_path.is_file():
        raise FileNotFoundError(f"Bulk DB not found: {database_path}")
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Bundled catalog not found: {catalog_path}")

    subjects = _table_count(database_path, "subjects")
    capabilities = _table_count(database_path, "capabilities")
    constraints = _table_count(database_path, "constraints")
    effects = _table_count(database_path, "effects")
    if min(subjects, capabilities, constraints, effects) <= 0:
        raise RuntimeError("Structured tables are unexpectedly empty.")

    bulk_engine = QueryEngine(ClaimStore(database_url=f"sqlite:///{database_path}"))
    ask = bulk_engine.ask(
        question="how do I create a local ssh port forward",
        tool_id_hint="ssh",
        limit=1,
    )
    if ask.fallback_recommended or not ask.answers:
        raise RuntimeError("Structured ask() smoke failed for ssh local port forward.")

    bundle_engine = QueryEngine(ClaimStore(database_url=f"sqlite:///{catalog_path}"))
    capabilities_response = bundle_engine.get_capabilities(
        subject_id="ssh-error-ssh-error-permission-denied-publickey",
        capability_types=["invocation"],
        limit=1,
    )
    if not capabilities_response.capabilities:
        raise RuntimeError("Bundled catalog get_capabilities() returned no invocation rows.")
    if capabilities_response.capabilities[0].source != "structured":
        raise RuntimeError("Bundled catalog returned a non-structured capability row.")

    effects_response = bundle_engine.get_effects(
        subject_id="ssh-error-ssh-error-permission-denied-publickey"
    )
    if not effects_response.effects:
        raise RuntimeError("Bundled catalog get_effects() returned no effect rows.")

    return {
        "subjects": subjects,
        "capabilities": capabilities,
        "constraints": constraints,
        "effects": effects,
        "ask_top": ask.answers[0].claim_id,
        "bundle_cap": capabilities_response.capabilities[0].capability_id,
        "bundle_effects": len(effects_response.effects),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(DEFAULT_DB))
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    args = parser.parse_args()

    result = run_smoke(
        database_path=Path(args.database),
        catalog_path=Path(args.catalog),
    )
    print(
        "Smoke OK:",
        f"subjects={result['subjects']}",
        f"capabilities={result['capabilities']}",
        f"constraints={result['constraints']}",
        f"effects={result['effects']}",
        f"ask_top={result['ask_top']}",
        f"bundle_cap={result['bundle_cap']}",
        f"bundle_effects={result['bundle_effects']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
