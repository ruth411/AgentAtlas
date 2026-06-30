"""Ingest canonical machine-readable curated tool source files into Ayiru."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.curated_tool_source import ingest_curated_tool_source, load_curated_tool_source  # noqa: E402
from app.services.structured_knowledge_store import StructuredKnowledgeStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    document = load_curated_tool_source(Path(args.file))
    store = StructuredKnowledgeStore(database_url=args.database)
    written = ingest_curated_tool_source(store, document, dry_run=args.dry_run)
    print(
        f"{document['family']} curated source ingest ({'dry-run' if args.dry_run else 'applied'}): "
        f"{written['subjects']} subjects, {written['capabilities']} caps, "
        f"{written['constraints']} constraints, {written['effects']} effects"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
