"""Validate and ingest all checked-in curated tool sources.

Rebuilds the structured rows for every `tools/tool_sources/*.v1.json` file and
optionally refreshes the bundled MCP catalog from the resulting bulk DB.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.curated_tool_source import ingest_curated_tool_source, load_curated_tool_source  # noqa: E402
from app.services.structured_knowledge_store import StructuredKnowledgeStore  # noqa: E402


TOOL_SOURCES_DIR = REPO_ROOT / "tools" / "tool_sources"
DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"
DEFAULT_BUNDLE = REPO_ROOT / "mcp" / "ayiru_mcp" / "data" / "catalog.db"


def _tool_source_files() -> list[Path]:
    return sorted(TOOL_SOURCES_DIR.glob("*.v1.json"))


def _bundle_families(database_path: Path) -> str:
    conn = sqlite3.connect(str(database_path))
    try:
        rows = conn.execute(
            "SELECT DISTINCT family FROM subjects ORDER BY family"
        ).fetchall()
    finally:
        conn.close()
    return ",".join(row[0] for row in rows)


def compile_all_curated_sources(
    *,
    database_url: str,
    rebuild_bundle: bool = False,
    bundle_output: str = str(DEFAULT_BUNDLE),
) -> dict[str, object]:
    files = _tool_source_files()
    if not files:
        raise SystemExit("No curated source files found under tools/tool_sources/")

    store = StructuredKnowledgeStore(database_url=database_url)
    now = datetime.now(timezone.utc)
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0}
    per_family: list[dict[str, object]] = []

    for path in files:
        document = load_curated_tool_source(path)
        written = ingest_curated_tool_source(store, document, now=now)
        for key, value in written.items():
            totals[key] += value
        per_family.append({"family": document["family"], **written})

    result: dict[str, object] = {
        "families": per_family,
        "totals": totals,
    }

    if rebuild_bundle:
        from tools.scripts.build_slim_catalog import build

        database_path = Path(database_url.removeprefix("sqlite:///")).resolve()
        build_counts = build(
            database_path,
            Path(bundle_output),
            _bundle_families(database_path).split(","),
            structured_only=True,
        )
        result["bundle_output"] = str(bundle_output)
        result["bundle_counts"] = build_counts

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=f"sqlite:///{DEFAULT_DB}",
        help="SQLAlchemy database URL for the bulk DB",
    )
    parser.add_argument(
        "--rebuild-bundle",
        action="store_true",
        help="Also rebuild the bundled MCP catalog after ingesting sources.",
    )
    parser.add_argument(
        "--bundle-output",
        default=str(DEFAULT_BUNDLE),
        help="Output path for the bundled catalog when --rebuild-bundle is set.",
    )
    args = parser.parse_args()

    result = compile_all_curated_sources(
        database_url=args.database,
        rebuild_bundle=args.rebuild_bundle,
        bundle_output=args.bundle_output,
    )
    totals = result["totals"]

    for family_result in result["families"]:
        print(
            f"{family_result['family']}: {family_result['subjects']} subjects, "
            f"{family_result['capabilities']} caps, {family_result['constraints']} constraints, "
            f"{family_result['effects']} effects"
        )

    print(
        f"\nTotal: {totals['subjects']} subjects, {totals['capabilities']} caps, "
        f"{totals['constraints']} constraints, {totals['effects']} effects"
    )

    if args.rebuild_bundle:
        print(f"Rebuilt bundled catalog at {args.bundle_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
