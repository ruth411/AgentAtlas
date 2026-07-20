"""Rebuild and audit the structured Ayiru product surfaces.

Default behavior is conservative: rebuild the bundled structured catalog from
the current bulk DB, run smoke tests, and print coverage + freshness reports.
Pass `--refresh-curated` to also re-ingest checked-in `tools/tool_sources/*.v1.json`
into the target DB before rebuilding the bundle.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.scripts.build_slim_catalog import build  # noqa: E402
from tools.scripts.compile_curated_sources import (  # noqa: E402
    DEFAULT_BUNDLE,
    DEFAULT_DB,
    _bundle_families,
    compile_all_curated_sources,
)
from tools.scripts.report_catalog_freshness import collect_family_freshness  # noqa: E402
from tools.scripts.report_tool_coverage import _band, collect_family_coverage  # noqa: E402
from tools.scripts.smoke_product import run_smoke  # noqa: E402


def _sqlite_database_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise SystemExit("Only sqlite:/// database URLs are supported by this script.")
    return Path(database_url.removeprefix("sqlite:///")).resolve()


def _print_top_coverage(database_path: Path) -> None:
    rows = collect_family_coverage(database_path)[:5]
    print("coverage_top:")
    for row in rows:
        print(
            f"  {row.family}: {row.breakdown.total:.2f} "
            f"({_band(row.breakdown.total)}; subjects={row.subjects}, caps={row.capabilities})"
        )


def _print_top_freshness(database_path: Path) -> None:
    rows = collect_family_freshness(database_path)[:5]
    print("freshness_watch:")
    for row in rows:
        print(
            f"  {row.family}: rows={row.row_count}, stale_90d={row.stale_90d}, "
            f"missing_ts={row.missing_timestamp_rows}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=f"sqlite:///{DEFAULT_DB}",
        help="SQLite database URL for the canonical structured DB.",
    )
    parser.add_argument(
        "--bundle-output",
        default=str(DEFAULT_BUNDLE),
        help="Output path for the bundled structured catalog.",
    )
    parser.add_argument(
        "--refresh-curated",
        action="store_true",
        help="Upsert checked-in tools/tool_sources/*.v1.json into the DB before rebuilding.",
    )
    parser.add_argument(
        "--skip-bundle",
        action="store_true",
        help="Skip rebuilding the bundled catalog.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip the structured bulk/bundle smoke test.",
    )
    parser.add_argument(
        "--skip-coverage",
        action="store_true",
        help="Skip the per-family coverage summary.",
    )
    parser.add_argument(
        "--skip-freshness",
        action="store_true",
        help="Skip the provenance freshness summary.",
    )
    args = parser.parse_args()

    database_path = _sqlite_database_path(args.database)
    bundle_path = Path(args.bundle_output).resolve()

    if args.refresh_curated:
        compiled = compile_all_curated_sources(
            database_url=args.database,
            rebuild_bundle=False,
        )
        totals = compiled["totals"]
        print(
            "curated_refresh:",
            f"subjects={totals['subjects']}",
            f"capabilities={totals['capabilities']}",
            f"constraints={totals['constraints']}",
            f"effects={totals['effects']}",
        )

    if not args.skip_bundle:
        build_counts = build(
            database_path,
            bundle_path,
            _bundle_families(database_path).split(","),
            structured_only=True,
        )
        print(
            "bundle_rebuild:",
            f"subjects={build_counts['subjects']}",
            f"capabilities={build_counts['capabilities']}",
            f"constraints={build_counts['constraints']}",
            f"effects={build_counts['effects']}",
            f"output={bundle_path}",
        )

    if not args.skip_smoke:
        smoke = run_smoke(database_path=database_path, catalog_path=bundle_path)
        print(
            "smoke:",
            f"bulk_subject_id={smoke['bulk_subject_id']}",
            f"bulk_cap={smoke['bulk_cap']}",
            f"bulk_effects={smoke['bulk_effects']}",
            f"bundle_subject_id={smoke['bundle_subject_id']}",
            f"bundle_cap={smoke['bundle_cap']}",
            f"bundle_effects={smoke['bundle_effects']}",
            f"bundle_action={smoke['bundle_action']}",
        )

    if not args.skip_coverage:
        _print_top_coverage(database_path)

    if not args.skip_freshness:
        _print_top_freshness(database_path)

    row_counts = {}
    conn = sqlite3.connect(str(database_path))
    try:
        for table in ("subjects", "capabilities", "constraints", "effects"):
            row_counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
    finally:
        conn.close()

    print(
        "totals:",
        f"subjects={row_counts['subjects']}",
        f"capabilities={row_counts['capabilities']}",
        f"constraints={row_counts['constraints']}",
        f"effects={row_counts['effects']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
