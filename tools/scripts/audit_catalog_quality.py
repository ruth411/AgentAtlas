"""Read-only catalog quality audit.

Usage:
    cd /Users/ruthwikdovala/Documents/ayiru
    backend/.venv/bin/python tools/scripts/audit_catalog_quality.py
    backend/.venv/bin/python tools/scripts/audit_catalog_quality.py --database backend/ayiru_v0.2_bulk.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.docs_ingestion import _find_page_chrome_markers  # noqa: E402


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _family(tool_id: str) -> str:
    if "-" not in tool_id:
        return tool_id
    return tool_id.split("-", 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=str(ROOT / "backend" / "ayiru_v0.2_bulk.db"),
        help="Path to the SQLite catalog to audit.",
    )
    args = parser.parse_args()

    db_path = Path(args.database).resolve()
    conn = _connect_read_only(db_path)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM knowledge_claims").fetchone()[0]
    print(f"database: {db_path}")
    print(f"total_claims: {total}")
    print("verification_status:")
    for row in conn.execute(
        "SELECT verification_status, COUNT(*) AS n "
        "FROM knowledge_claims GROUP BY verification_status ORDER BY n DESC"
    ):
        print(f"  {row['verification_status']}: {row['n']}")

    accepted_rows = conn.execute(
        "SELECT tool_id, subject, statement FROM knowledge_claims "
        "WHERE verification_status='accepted'"
    ).fetchall()
    contaminated: list[tuple[str, str, list[str]]] = []
    contaminated_by_tool: Counter[str] = Counter()
    contaminated_by_family: Counter[str] = Counter()
    for row in accepted_rows:
        markers = _find_page_chrome_markers(row["statement"])
        if not markers:
            continue
        contaminated.append((row["tool_id"], row["subject"], markers))
        contaminated_by_tool[row["tool_id"]] += 1
        contaminated_by_family[_family(row["tool_id"])] += 1

    print(f"accepted_contaminated: {len(contaminated)}")
    if contaminated_by_tool:
        print("top_contaminated_tool_ids:")
        for tool_id, count in contaminated_by_tool.most_common(10):
            print(f"  {tool_id}: {count}")

    if contaminated_by_family:
        print("top_contaminated_families:")
        for family, count in contaminated_by_family.most_common(10):
            print(f"  {family}: {count}")

    print("weakest_families_by_acceptance_ratio:")
    family_rows = conn.execute(
        "SELECT tool_id, verification_status FROM knowledge_claims"
    ).fetchall()
    family_counts: Counter[str] = Counter()
    family_accepted: Counter[str] = Counter()
    for row in family_rows:
        family = _family(row["tool_id"])
        family_counts[family] += 1
        if row["verification_status"] == "accepted":
            family_accepted[family] += 1
    weakest = sorted(
        (
            (family_accepted[family] / family_counts[family], family_accepted[family], family_counts[family], family)
            for family in family_counts
        ),
        key=lambda item: (item[0], -item[2], item[3]),
    )[:10]
    for ratio, accepted, count, family in weakest:
        print(f"  {family}: {accepted}/{count} accepted ({ratio:.1%})")

    if contaminated:
        print("sample_contaminated_accepted_claims:")
        for tool_id, subject, markers in contaminated[:10]:
            print(f"  [{tool_id}] {subject} :: {', '.join(markers)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
