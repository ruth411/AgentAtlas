"""Build a slim catalog SQLite database for the `ayiru-mcp` wheel.

Reads from a full source database (the depth-pass result of running every
ingestion lane against the seed scripts), filters to the requested tool
families, and copies the rows the read-only MCP server actually needs:
``knowledge_claims``, ``evidence``, ``verification_results``, and the
``canonical_tool_specs`` / ``canonical_workflow_specs`` rows for the
selected tools. Server-side state (``audit_events``, ``ingestion_runs``,
``raw_ingestion_artifacts``, ``human_reviews``, ``docs_fetch_cache``) is
omitted — that data is operational, not query-time.

Embeddings live in ``knowledge_claims.embedding`` (JSON text), so they
come along for free with the row copy. The query engine will fall back
to lexical-only ranking when a row's embedding is NULL.

Usage:

    python tools/scripts/build_slim_catalog.py \\
        --source backend/ayiru_v0.2_bulk.db \\
        --tool-families gh \\
        --output mcp/ayiru_mcp/data/catalog.db

``--tool-families`` is a comma-separated list of family prefixes. ``gh``
matches every ``tool_id`` starting with ``gh`` followed by either end
of string or a hyphen (so ``gh``, ``gh-cli``, ``gh-recipes`` match;
``ghost``, ``ghidra`` do NOT). This mirrors the five-surface decomposition
without needing the caller to enumerate every surface.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Read-only tables `QueryEngine.ask()`, `validate_command`, `search_tools`,
# `get_tool_spec`, `explain_risk`, and `get_safe_workflow` actually touch.
# Order matters for foreign-key-style copy: parents (claims) before
# children (evidence, verification_results).
_TABLES_FILTERED_BY_TOOL = (
    "knowledge_claims",
    "evidence",
    "verification_results",
    "canonical_tool_specs",
    "canonical_workflow_specs",
)


def _family_match_predicate(families: list[str]) -> tuple[str, list[str]]:
    """SQL `WHERE` predicate + bind params for a tool-id family match.

    Matches an exact family name (``tool_id = 'gh'``) OR a five-surface
    decomposition variant (``tool_id LIKE 'gh-%'``). The ``LIKE`` pattern
    is the standard SQL form; SQLite ``%`` matches any sequence of chars
    including the empty string, so ``gh-%`` matches ``gh-cli``, ``gh-config``,
    etc., but NOT ``gh`` itself — the exact-match arm handles that.
    """
    clauses: list[str] = []
    params: list[str] = []
    for family in families:
        clauses.append("tool_id = ? OR tool_id LIKE ?")
        params.extend([family, f"{family}-%"])
    return "(" + " OR ".join(clauses) + ")", params


def _copy_claims(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    families: list[str],
) -> int:
    """Copy `knowledge_claims` rows matching the requested families.

    Returns the number of rows copied. We use ``INSERT OR REPLACE`` so
    re-running against a partially-populated output is idempotent.
    """
    predicate, params = _family_match_predicate(families)
    source_cursor = source.execute(
        f"SELECT * FROM knowledge_claims WHERE {predicate}", params
    )
    columns = [d[0] for d in source_cursor.description]
    column_list = ",".join(columns)
    placeholders = ",".join(["?"] * len(columns))
    rows = source_cursor.fetchall()
    if rows:
        target.executemany(
            f"INSERT OR REPLACE INTO knowledge_claims ({column_list}) VALUES ({placeholders})",
            rows,
        )
    return len(rows)


def _copy_child_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    parent_claim_ids: list[str],
) -> int:
    """Copy rows from a table that references `knowledge_claims.claim_id`.

    Used for `evidence` and `verification_results`. Both tables join back
    to a claim via `claim_id`, so we filter by membership in the copied
    parent set rather than by `tool_id`.
    """
    if not parent_claim_ids:
        return 0
    placeholders = ",".join(["?"] * len(parent_claim_ids))
    source_cursor = source.execute(
        f"SELECT * FROM {table} WHERE claim_id IN ({placeholders})",
        parent_claim_ids,
    )
    columns = [d[0] for d in source_cursor.description]
    column_list = ",".join(columns)
    insert_placeholders = ",".join(["?"] * len(columns))
    rows = source_cursor.fetchall()
    if rows:
        target.executemany(
            f"INSERT OR REPLACE INTO {table} ({column_list}) VALUES ({insert_placeholders})",
            rows,
        )
    return len(rows)


def _copy_canonical_specs(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    families: list[str],
) -> int:
    """Copy `canonical_tool_specs` / `canonical_workflow_specs` rows for the families."""
    predicate, params = _family_match_predicate(families)
    source_cursor = source.execute(
        f"SELECT * FROM {table} WHERE {predicate}", params
    )
    columns = [d[0] for d in source_cursor.description]
    column_list = ",".join(columns)
    placeholders = ",".join(["?"] * len(columns))
    rows = source_cursor.fetchall()
    if rows:
        target.executemany(
            f"INSERT OR REPLACE INTO {table} ({column_list}) VALUES ({placeholders})",
            rows,
        )
    return len(rows)


def _migrate_target_schema(target_path: Path) -> None:
    """Create the empty target DB with the schema `ClaimStore` expects.

    We invoke alembic via the existing helper rather than `CREATE TABLE`
    by hand so the schema follows whatever revision is current. This is
    also what the production server runs at first boot.
    """
    # Import lazily so the script doesn't pull alembic just to print --help.
    from alembic import command

    from app.services.alembic_config import make_alembic_config

    config = make_alembic_config(database_url=f"sqlite:///{target_path}")
    command.upgrade(config, "head")


_STRUCTURED_PARENT_TABLES = ("subjects",)
_STRUCTURED_CHILD_TABLES = ("capabilities", "constraints", "effects")


def _copy_structured_subjects(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    families: list[str],
) -> tuple[int, list[str]]:
    """Copy `subjects` rows whose `family` is one of the requested families.

    Returns the row count and the list of `subject_id`s copied (used to
    filter the dependent capability / constraint / effect rows)."""
    placeholders = ",".join(["?"] * len(families))
    cursor = source.execute(
        f"SELECT * FROM subjects WHERE family IN ({placeholders})",
        families,
    )
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    if not rows:
        return 0, []
    col_list = ",".join(columns)
    inserts = ",".join(["?"] * len(columns))
    target.executemany(
        f"INSERT OR REPLACE INTO subjects ({col_list}) VALUES ({inserts})",
        rows,
    )
    subject_id_idx = columns.index("subject_id")
    return len(rows), [row[subject_id_idx] for row in rows]


def _copy_structured_child(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    subject_ids: list[str],
) -> int:
    if not subject_ids:
        return 0
    placeholders = ",".join(["?"] * len(subject_ids))
    cursor = source.execute(
        f"SELECT * FROM {table} WHERE subject_id IN ({placeholders})",
        subject_ids,
    )
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    if not rows:
        return 0
    col_list = ",".join(columns)
    inserts = ",".join(["?"] * len(columns))
    target.executemany(
        f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({inserts})",
        rows,
    )
    return len(rows)


def build(
    source_path: Path,
    output_path: Path,
    families: list[str],
    *,
    structured_only: bool = False,
) -> dict[str, int]:
    """Bake a slim bundled catalog.

    When ``structured_only=True`` (the default for v0.2+ MCP wheels), only the
    structured-knowledge tables ship; prose claims / evidence / verification
    rows are skipped. The `QueryEngine` reads structured records first and
    only falls through to prose projection when no structured row exists,
    so a structured-only bundle is correct as long as every requested
    family has structured coverage.
    """
    source_path = source_path.resolve()
    output_path = output_path.resolve()

    if not source_path.is_file():
        raise FileNotFoundError(f"Source DB not found: {source_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    _migrate_target_schema(output_path)

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(str(output_path))
    try:
        counts: dict[str, int] = {}

        # Structured tables — the v0.2 headline content.
        subject_count, subject_ids = _copy_structured_subjects(source, target, families)
        counts["subjects"] = subject_count
        for table in _STRUCTURED_CHILD_TABLES:
            counts[table] = _copy_structured_child(source, target, table, subject_ids)

        if not structured_only:
            # Legacy prose tables — kept for backward-compat self-hosting.
            counts["knowledge_claims"] = _copy_claims(source, target, families)
            predicate, params = _family_match_predicate(families)
            claim_ids = [
                row[0]
                for row in source.execute(
                    f"SELECT claim_id FROM knowledge_claims WHERE {predicate}", params
                )
            ]
            counts["evidence"] = _copy_child_table(source, target, "evidence", claim_ids)
            counts["verification_results"] = _copy_child_table(
                source, target, "verification_results", claim_ids
            )
            counts["canonical_tool_specs"] = _copy_canonical_specs(
                source, target, "canonical_tool_specs", families
            )
            # `canonical_workflow_specs` is keyed by `workflow_id`, not `tool_id`,
            # so we can't filter it by family.
            counts["canonical_workflow_specs"] = 0

        target.commit()
        target.execute("VACUUM")
        return counts
    finally:
        source.close()
        target.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to the source SQLite DB (e.g. backend/ayiru_v0.2_bulk.db).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the slim catalog DB (created/overwritten).",
    )
    parser.add_argument(
        "--tool-families",
        default="gh",
        help="Comma-separated list of tool-id family prefixes to include. Default: gh.",
    )
    parser.add_argument(
        "--structured-only",
        action="store_true",
        help=(
            "Skip the prose `knowledge_claims` / `evidence` / "
            "`verification_results` / `canonical_tool_specs` tables; ship "
            "only structured `subjects` / `capabilities` / `constraints` / "
            "`effects`. Default for v0.2+ MCP wheels."
        ),
    )
    args = parser.parse_args(argv)
    args.source = args.source.resolve()
    args.output = args.output.resolve()
    families = [item.strip() for item in args.tool_families.split(",") if item.strip()]
    if not families:
        parser.error("--tool-families must contain at least one family name.")

    counts = build(
        args.source, args.output, families, structured_only=args.structured_only,
    )
    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"Wrote {args.output} ({size_mb:.2f} MB):")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
