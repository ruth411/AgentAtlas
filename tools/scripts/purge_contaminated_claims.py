"""Demote `accepted` claims whose statement is scraped page chrome.

The 2026-06 catalog audit found ~739 `accepted` claims whose `statement`
is navigation/banner chrome (``hashiconf_banner``, ``skip_to_content``,
``ansible_forum_banner`` …). They were accepted *before* the docs
sanitizer learned to strip that chrome, so the cleanup never reached
them — they sit in the catalog wearing a "verified / accepted" badge.

This script is the deterministic, network-free first half of the WS3
cleanup: it finds every `accepted` claim the canonical chrome detector
(`_find_page_chrome_markers` in
``backend/app/services/docs_ingestion.py``) flags, and demotes it to
``requires_human_review``. It does **not** delete anything — the claim,
its evidence, and its citation stay; only the trust badge drops. The
companion ``reingest_junk_claims.py`` then re-fetches the salvageable
ones; whatever it cleans can re-qualify for acceptance via the
orchestrator.

Every demotion writes an `audit_events` row so the provenance of the
status change is recoverable.

Usage:
    python tools/scripts/purge_contaminated_claims.py --dry-run
    python tools/scripts/purge_contaminated_claims.py --database backend/ayiru_v0.2_bulk.db
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.docs_ingestion import _find_page_chrome_markers  # noqa: E402

_DEMOTED_STATUS = "requires_human_review"
_ACTOR = "ayiru-purge"
# A purge records a (negative) verification outcome — the claim's statement is
# page chrome, so it no longer meets acceptance. `verification_recorded` is the
# audit category for a verification-status change; `details_json` + `actor`
# carry the purge-specific provenance (chrome markers, from/to status).
_EVENT_TYPE = "verification_recorded"
_REASON = "contamination_purge"


def purge(conn: sqlite3.Connection, *, dry_run: bool) -> dict[str, object]:
    conn.row_factory = sqlite3.Row
    accepted = conn.execute(
        "SELECT claim_id, tool_id, subject, statement FROM knowledge_claims "
        "WHERE verification_status = 'accepted'"
    ).fetchall()

    demoted: list[tuple[str, str, list[str]]] = []
    by_family: dict[str, int] = {}
    for row in accepted:
        markers = _find_page_chrome_markers(row["statement"] or "")
        if not markers:
            continue
        demoted.append((row["claim_id"], row["tool_id"], markers))
        family = row["tool_id"].split("-", 1)[0]
        by_family[family] = by_family.get(family, 0) + 1

    if not dry_run and demoted:
        now = datetime.now(timezone.utc).isoformat(sep=" ")
        for claim_id, tool_id, markers in demoted:
            conn.execute(
                "UPDATE knowledge_claims SET verification_status = ? WHERE claim_id = ?",
                (_DEMOTED_STATUS, claim_id),
            )
            conn.execute(
                "INSERT INTO audit_events "
                "(event_id, event_type, entity_type, entity_id, actor, details_json, created_at) "
                "VALUES (?, ?, 'claim', ?, ?, ?, ?)",
                (
                    f"audit_{uuid4().hex}",
                    _EVENT_TYPE,
                    claim_id,
                    _ACTOR,
                    json.dumps(
                        {
                            "reason": _REASON,
                            "tool_id": tool_id,
                            "from_status": "accepted",
                            "to_status": _DEMOTED_STATUS,
                            "chrome_markers": markers,
                        }
                    ),
                    now,
                ),
            )
        conn.commit()

    return {
        "total_accepted": len(accepted),
        "demoted": len(demoted),
        "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1])),
        "applied": not dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=str(ROOT / "backend" / "ayiru_v0.2_bulk.db"),
        help="Path to the SQLite catalog.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be demoted without writing.",
    )
    args = parser.parse_args(argv)

    conn = sqlite3.connect(Path(args.database).resolve())
    try:
        report = purge(conn, dry_run=args.dry_run)
    finally:
        conn.close()

    print(f"total_accepted: {report['total_accepted']}")
    print(f"demoted_to_{_DEMOTED_STATUS}: {report['demoted']} (applied={report['applied']})")
    if report["by_family"]:
        print("by_family:")
        for family, count in report["by_family"].items():  # type: ignore[union-attr]
            print(f"  {family}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
