#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "core"))

from app.services.structured_cli_ingestion import StructuredCliIngestionService  # noqa: E402
from app.services.structured_knowledge_store import StructuredKnowledgeStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Structured-first gh CLI ingestion.")
    parser.add_argument(
        "--database",
        default=None,
        help="sqlite:/// URL for the target catalog DB. Falls back to AYIRU_DATABASE_URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate gh help output without writing rows.",
    )
    parser.add_argument(
        "--subject-id",
        action="append",
        default=[],
        help="Restrict ingestion to one or more subject ids, e.g. gh-pr-create.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = StructuredKnowledgeStore(database_url=args.database)
    report = StructuredCliIngestionService(store).ingest_gh(
        dry_run=args.dry_run,
        subject_ids=args.subject_id or None,
    )
    mode = "dry-run" if args.dry_run else "applied"
    print(f"structured gh ingest ({mode})")
    print(f"  subjects: {report.subjects_written}")
    print(f"  capabilities: {report.capability_rows_written}")
    print(f"  constraints: {report.constraint_rows_written}")
    print(f"  effects: {report.effect_rows_written}")
    if report.subject_ids:
        print("  subject_ids:")
        for subject_id in report.subject_ids:
            print(f"    - {subject_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
