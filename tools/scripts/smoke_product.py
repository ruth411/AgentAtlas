"""Smoke-test the public structured Ayiru product surfaces."""

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


def _probe_structured_subject(
    engine: QueryEngine,
    *,
    subject_hint: str,
    action_intent: str,
) -> dict[str, object]:
    resolution = engine.resolve_subject(subject_hint=subject_hint, limit=1)
    if not resolution.matches:
        raise RuntimeError(f"resolve_subject() returned no matches for '{subject_hint}'.")
    subject_id = resolution.matches[0].subject_id

    capabilities_response = engine.get_capabilities(
        subject_id=subject_id,
        capability_types=["invocation"],
        limit=1,
    )
    if not capabilities_response.capabilities:
        raise RuntimeError(f"get_capabilities() returned no invocation rows for '{subject_id}'.")
    if capabilities_response.capabilities[0].source != "structured":
        raise RuntimeError(f"get_capabilities() returned a non-structured row for '{subject_id}'.")

    effects_response = engine.get_effects(subject_id=subject_id)
    if not effects_response.effects:
        raise RuntimeError(f"get_effects() returned no effect rows for '{subject_id}'.")

    action_response = engine.resolve_action(
        subject_id=subject_id,
        action_intent=action_intent,
        limit=1,
    )
    if action_response.top_capability is None:
        raise RuntimeError(f"resolve_action() returned no top capability for '{subject_id}'.")

    return {
        "subject_id": subject_id,
        "capability_id": capabilities_response.capabilities[0].capability_id,
        "effect_count": len(effects_response.effects),
        "action_capability_id": action_response.top_capability.capability_id,
    }


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

    bulk_probe = _probe_structured_subject(
        QueryEngine(ClaimStore(database_url=f"sqlite:///{database_path}")),
        subject_hint="gh pr create",
        action_intent="create a pull request",
    )
    bundle_probe = _probe_structured_subject(
        QueryEngine(ClaimStore(database_url=f"sqlite:///{catalog_path}")),
        subject_hint="gh pr create",
        action_intent="create a pull request",
    )

    return {
        "subjects": subjects,
        "capabilities": capabilities,
        "constraints": constraints,
        "effects": effects,
        "bulk_subject_id": bulk_probe["subject_id"],
        "bulk_cap": bulk_probe["capability_id"],
        "bulk_effects": bulk_probe["effect_count"],
        "bundle_subject_id": bundle_probe["subject_id"],
        "bundle_cap": bundle_probe["capability_id"],
        "bundle_effects": bundle_probe["effect_count"],
        "bundle_action": bundle_probe["action_capability_id"],
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
        f"bulk_subject_id={result['bulk_subject_id']}",
        f"bulk_cap={result['bulk_cap']}",
        f"bulk_effects={result['bulk_effects']}",
        f"bundle_subject_id={result['bundle_subject_id']}",
        f"bundle_cap={result['bundle_cap']}",
        f"bundle_effects={result['bundle_effects']}",
        f"bundle_action={result['bundle_action']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
