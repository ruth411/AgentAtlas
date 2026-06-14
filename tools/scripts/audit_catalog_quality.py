"""Read-only catalog quality audit.

Usage:
    cd /Users/ruthwikdovala/Documents/ayiru
    backend/.venv/bin/python tools/scripts/audit_catalog_quality.py
    backend/.venv/bin/python tools/scripts/audit_catalog_quality.py --database backend/ayiru_v0.2_bulk.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.docs_ingestion import _find_page_chrome_markers  # noqa: E402


_STRUCTURED_TABLES = ("subjects", "capabilities", "constraints", "effects")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _family(tool_id: str) -> str:
    if "-" not in tool_id:
        return tool_id
    return tool_id.split("-", 1)[0]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def _subject_ids_from_table(conn: sqlite3.Connection, table: str) -> set[str]:
    columns = _table_columns(conn, table)
    if "subject_id" not in columns:
        return set()
    return {
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT subject_id FROM {table} WHERE subject_id IS NOT NULL"
        )
    }


def _baseline_subject_ids(conn: sqlite3.Connection) -> set[str]:
    subject_rows = _subject_ids_from_table(conn, "subjects")
    if subject_rows:
        return subject_rows
    return {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT tool_id FROM knowledge_claims WHERE tool_id IS NOT NULL"
        )
    }


def _baseline_source(conn: sqlite3.Connection) -> str:
    return "subjects.subject_id" if _subject_ids_from_table(conn, "subjects") else "knowledge_claims.tool_id"


def _group_by_family(subject_ids: Iterable[str]) -> dict[str, set[str]]:
    families: dict[str, set[str]] = {}
    for subject_id in subject_ids:
        families.setdefault(_family(subject_id), set()).add(subject_id)
    return families


def _structured_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    baseline_subjects = _baseline_subject_ids(conn)
    grouped = _group_by_family(baseline_subjects)
    structured_ids = {
        table: _subject_ids_from_table(conn, table)
        for table in _STRUCTURED_TABLES
    }
    per_family: list[dict[str, Any]] = []
    for family in sorted(grouped):
        subject_ids = grouped[family]
        total = len(subject_ids)
        row = {
            "family": family,
            "total_subjects": total,
        }
        for table in _STRUCTURED_TABLES:
            covered = len(subject_ids & structured_ids[table])
            key = f"{table}_covered"
            row[key] = covered
            row[f"{table}_ratio"] = (covered / total) if total else 0.0
        per_family.append(row)

    return {
        "baseline_source": _baseline_source(conn),
        "total_subjects": len(baseline_subjects),
        "structured_tables": {
            table: _table_exists(conn, table) for table in _STRUCTURED_TABLES
        },
        "subjects_with_rows": len(baseline_subjects & structured_ids["subjects"]),
        "subjects_with_capabilities": len(baseline_subjects & structured_ids["capabilities"]),
        "subjects_with_constraints": len(baseline_subjects & structured_ids["constraints"]),
        "subjects_with_effects": len(baseline_subjects & structured_ids["effects"]),
        "per_family": per_family,
    }


def _count_gh_contract_subjects() -> int:
    contract = json.loads((ROOT / "contracts" / "docs_ingestion_sources.v1.json").read_text())
    unique_subjects: set[str] = set()
    for tool_id, entry in contract["tools"].items():
        if tool_id not in {"gh-cli", "gh-config", "gh-workflows"}:
            continue
        for source in entry.get("sources", []):
            subject = str(source.get("subject", "")).strip()
            if subject:
                unique_subjects.add(subject)
    return len(unique_subjects)


def _gh_structured_subcommand_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    subject_columns = _table_columns(conn, "subjects")
    structured = 0
    if "family" in subject_columns:
        structured = conn.execute(
            "SELECT COUNT(DISTINCT subject_id) FROM subjects WHERE family='gh'"
        ).fetchone()[0]
    elif "subject_id" in subject_columns:
        structured = conn.execute(
            "SELECT COUNT(DISTINCT subject_id) FROM subjects "
            "WHERE subject_id = 'gh' OR subject_id LIKE 'gh-%'"
        ).fetchone()[0]

    return structured, _count_gh_contract_subjects()


# Trailing subcommand tokens that denote an irreversible destructive operation.
# Kept in sync with `_DESTRUCTIVE_VERBS` in
# `backend/app/services/structured_cli_ingestion.py`.
_DESTRUCTIVE_VERBS = frozenset(
    {"delete", "remove", "rm", "purge", "prune", "destroy", "revoke", "uninstall"}
)


def _destructive_effect_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every subject whose trailing token is a destructive verb must have an
    effect row flagged `destructive=1`. A miss is a safety-classification bug:
    an agent would be told a data-destroying command is safe."""
    if not _table_exists(conn, "subjects") or not _table_exists(conn, "effects"):
        return {"checked": 0, "misses": []}
    rows = conn.execute("SELECT subject_id FROM subjects").fetchall()
    destructive_subjects = [
        row["subject_id"]
        for row in rows
        if row["subject_id"].split("-")[-1] in _DESTRUCTIVE_VERBS
    ]
    misses: list[str] = []
    for subject_id in destructive_subjects:
        flagged = conn.execute(
            "SELECT COUNT(*) FROM effects WHERE subject_id=? AND destructive=1",
            (subject_id,),
        ).fetchone()[0]
        if not flagged:
            misses.append(subject_id)
    return {"checked": len(destructive_subjects), "misses": sorted(misses)}


def audit_catalog(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM knowledge_claims").fetchone()[0]
    verification_rows = list(
        conn.execute(
            "SELECT verification_status, COUNT(*) AS n "
            "FROM knowledge_claims GROUP BY verification_status ORDER BY n DESC"
        )
    )
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
            (
                family_accepted[family] / family_counts[family],
                family_accepted[family],
                family_counts[family],
                family,
            )
            for family in family_counts
        ),
        key=lambda item: (item[0], -item[2], item[3]),
    )[:10]

    gh_structured, gh_total = _gh_structured_subcommand_counts(conn)

    return {
        "total_claims": total,
        "verification_status": [
            (row["verification_status"], row["n"]) for row in verification_rows
        ],
        "accepted_contaminated": len(contaminated),
        "top_contaminated_tool_ids": contaminated_by_tool.most_common(10),
        "top_contaminated_families": contaminated_by_family.most_common(10),
        "weakest_families": weakest,
        "sample_contaminated": contaminated[:10],
        "structured_coverage": _structured_coverage(conn),
        "gh_structured_subcommands": (gh_structured, gh_total),
        "destructive_effect_coverage": _destructive_effect_coverage(conn),
    }


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

    report = audit_catalog(conn)
    print(f"database: {db_path}")
    print(f"total_claims: {report['total_claims']}")
    print("verification_status:")
    for status, count in report["verification_status"]:
        print(f"  {status}: {count}")

    print(f"accepted_contaminated: {report['accepted_contaminated']}")
    if report["top_contaminated_tool_ids"]:
        print("top_contaminated_tool_ids:")
        for tool_id, count in report["top_contaminated_tool_ids"]:
            print(f"  {tool_id}: {count}")

    if report["top_contaminated_families"]:
        print("top_contaminated_families:")
        for family, count in report["top_contaminated_families"]:
            print(f"  {family}: {count}")

    print("weakest_families_by_acceptance_ratio:")
    for ratio, accepted, count, family in report["weakest_families"]:
        print(f"  {family}: {accepted}/{count} accepted ({ratio:.1%})")

    structured = report["structured_coverage"]
    print("structured_tables:")
    for table, exists in structured["structured_tables"].items():
        print(f"  {table}: {'present' if exists else 'missing'}")

    print("structured_subjects:")
    print(f"  baseline_source: {structured['baseline_source']}")
    print(f"  total_subjects: {structured['total_subjects']}")
    print(f"  with_subject_rows: {structured['subjects_with_rows']}")
    print(f"  with_capability_rows: {structured['subjects_with_capabilities']}")
    print(f"  with_constraint_rows: {structured['subjects_with_constraints']}")
    print(f"  with_effect_rows: {structured['subjects_with_effects']}")

    print("structured_coverage_by_family:")
    for family_row in structured["per_family"]:
        print(
            "  "
            f"{family_row['family']}: "
            f"subjects={family_row['subjects_covered']}/{family_row['total_subjects']} "
            f"({family_row['subjects_ratio']:.1%}), "
            f"capabilities={family_row['capabilities_covered']}/{family_row['total_subjects']} "
            f"({family_row['capabilities_ratio']:.1%}), "
            f"constraints={family_row['constraints_covered']}/{family_row['total_subjects']} "
            f"({family_row['constraints_ratio']:.1%}), "
            f"effects={family_row['effects_covered']}/{family_row['total_subjects']} "
            f"({family_row['effects_ratio']:.1%})"
        )

    gh_structured, gh_total = report["gh_structured_subcommands"]
    print(f"gh_structured_subcommands: {gh_structured}/{gh_total}")

    destructive = report["destructive_effect_coverage"]
    print(
        "destructive_effect_coverage: "
        f"{destructive['checked'] - len(destructive['misses'])}/{destructive['checked']} "
        "destructive-verb subjects flagged"
    )
    if destructive["misses"]:
        print("  UNFLAGGED destructive subjects (safety bug):")
        for subject_id in destructive["misses"]:
            print(f"    {subject_id}")

    if report["sample_contaminated"]:
        print("sample_contaminated_accepted_claims:")
        for tool_id, subject, markers in report["sample_contaminated"]:
            print(f"  [{tool_id}] {subject} :: {', '.join(markers)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
