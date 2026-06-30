"""Report structured catalog freshness from provenance.imported_at timestamps."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class FamilyFreshness:
    family: str
    row_count: int
    rows_with_timestamp: int
    missing_timestamp_rows: int
    oldest_imported_at: datetime | None
    newest_imported_at: datetime | None
    stale_30d: int
    stale_90d: int
    stale_180d: int


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _imported_at_from_detail(detail_json: str) -> datetime | None:
    try:
        detail = json.loads(detail_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(detail, dict):
        return None
    provenance = detail.get("provenance")
    if isinstance(provenance, dict):
        parsed = _parse_timestamp(provenance.get("imported_at"))
        if parsed is not None:
            return parsed
    return _parse_timestamp(detail.get("imported_at"))


def _band(row: FamilyFreshness) -> str:
    if row.rows_with_timestamp == 0:
        return "unknown"
    if row.stale_180d > 0:
        return "stale"
    if row.stale_90d > 0:
        return "aging"
    if row.stale_30d > 0:
        return "recent"
    return "fresh"


def collect_family_freshness(
    database_path: Path,
    *,
    now: datetime | None = None,
) -> list[FamilyFreshness]:
    as_of = now or datetime.now(timezone.utc)
    conn = sqlite3.connect(str(database_path))
    try:
        rows = conn.execute(
            """
            SELECT s.family, c.detail_json
            FROM capabilities c
            JOIN subjects s ON s.subject_id = c.subject_id
            UNION ALL
            SELECT s.family, c.detail_json
            FROM constraints c
            JOIN subjects s ON s.subject_id = c.subject_id
            UNION ALL
            SELECT s.family, e.detail_json
            FROM effects e
            JOIN subjects s ON s.subject_id = e.subject_id
            """
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[str, list[datetime]] = {}
    missing: dict[str, int] = {}
    totals: dict[str, int] = {}
    for family, detail_json in rows:
        totals[family] = totals.get(family, 0) + 1
        imported_at = _imported_at_from_detail(detail_json)
        if imported_at is None:
            missing[family] = missing.get(family, 0) + 1
            continue
        grouped.setdefault(family, []).append(imported_at)

    result: list[FamilyFreshness] = []
    for family in sorted(totals):
        timestamps = grouped.get(family, [])
        stale_30d = stale_90d = stale_180d = 0
        for timestamp in timestamps:
            age_days = (as_of - timestamp).days
            stale_30d += int(age_days > 30)
            stale_90d += int(age_days > 90)
            stale_180d += int(age_days > 180)
        result.append(
            FamilyFreshness(
                family=family,
                row_count=totals[family],
                rows_with_timestamp=len(timestamps),
                missing_timestamp_rows=missing.get(family, 0),
                oldest_imported_at=min(timestamps) if timestamps else None,
                newest_imported_at=max(timestamps) if timestamps else None,
                stale_30d=stale_30d,
                stale_90d=stale_90d,
                stale_180d=stale_180d,
            )
        )

    return sorted(
        result,
        key=lambda row: (
            -row.stale_90d,
            -(row.missing_timestamp_rows),
            row.family,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=0, help="limit output rows; 0 = all")
    args = parser.parse_args()

    rows = collect_family_freshness(Path(args.database))
    if args.top > 0:
        rows = rows[: args.top]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "family": row.family,
                        "band": _band(row),
                        "row_count": row.row_count,
                        "rows_with_timestamp": row.rows_with_timestamp,
                        "missing_timestamp_rows": row.missing_timestamp_rows,
                        "oldest_imported_at": row.oldest_imported_at.isoformat()
                        if row.oldest_imported_at
                        else None,
                        "newest_imported_at": row.newest_imported_at.isoformat()
                        if row.newest_imported_at
                        else None,
                        "stale_30d": row.stale_30d,
                        "stale_90d": row.stale_90d,
                        "stale_180d": row.stale_180d,
                    }
                    for row in rows
                ],
                indent=2,
            )
        )
        return 0

    print("family\tband\trows\twith_timestamp\tmissing\tstale_30d\tstale_90d\tstale_180d")
    for row in rows:
        print(
            f"{row.family}\t{_band(row)}\t{row.row_count}\t{row.rows_with_timestamp}\t"
            f"{row.missing_timestamp_rows}\t{row.stale_30d}\t{row.stale_90d}\t{row.stale_180d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
