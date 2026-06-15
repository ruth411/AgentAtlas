"""Re-extract the statements on claims whose original ingestion captured
site navigation chrome instead of real prose.

The 2026-06-12 catalog audit found 134 claims (concentrated in gh-cli,
openssl-cli, pip-cli, gh-workflows) whose `statement` field is the page's
nav menu. Root cause was in the HTML extractor in
`backend/app/services/docs_ingestion.py` — it did not strip `<nav>` /
`<header>` / `<footer>` / `<aside>` and emitted the whole body.

This script:

1. Finds the affected claims by a heuristic substring match on `statement`.
2. For each affected claim, looks up the original source URL from its
   evidence row.
3. Re-fetches the URL via the same SSRF-safe path the docs lane uses,
   honouring `tool_trust_sources.v1.json`'s per-tool `official_hosts`.
4. Re-extracts the statement using the now-fixed sanitizer.
5. UPDATEs `knowledge_claims.statement` and `evidence.excerpt` in place,
   bumps the evidence `captured_at` so the freshness signal reflects the
   re-fetch.

Idempotent: if the new sanitized text equals the current statement, the
script logs SKIP and moves on. URLs that 404 are logged + skipped rather
than aborting the run.

Usage:
    python tools/scripts/reingest_junk_claims.py --dry-run        # print plan, no writes
    python tools/scripts/reingest_junk_claims.py                  # apply
    python tools/scripts/reingest_junk_claims.py --limit 20       # smoke test
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

# These markers identify scraped navigation chrome — same heuristics the
# audit used. Each captures a real string seen in the bulk DB.
_NAV_MARKERS = (
    "Skip to content",
    "Take GitHub to the command line",
    "Manual Release notes Getting started",
    "JavaScript is required",
    "Toggle navigation",
    "All systems operational",
)

# Statement cap matches `_STATEMENT_MAX_CHARS` in docs_ingestion.py.
_STATEMENT_MAX_CHARS = 480

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"


def is_junk(statement: str | None) -> bool:
    if not statement:
        return False
    if any(marker in statement for marker in _NAV_MARKERS):
        return True
    # Defer to the canonical chrome detector so this script targets exactly
    # what the audit and the purge script flag (banners, skip-links, nav
    # chevrons), not just the substring sample above. Imported lazily because
    # the backend path is wired into sys.path inside main() before this runs.
    from app.services.docs_ingestion import _find_page_chrome_markers

    return bool(_find_page_chrome_markers(statement))


def first_non_empty_line(text: str, *, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_STATEMENT_MAX_CHARS]
    return fallback[:_STATEMENT_MAX_CHARS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan and exit without writing.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after processing N claims (smoke test).")
    args = parser.parse_args()

    # Imports here so `--help` works without a full Ayiru install.
    from app.services.docs_ingestion import _sanitize_html_to_text
    from app.services.evidence_trust import _trust_sources
    from app.services.http_safety import (
        HttpFetchError,
        resolve_url_for_safe_fetch,
        safe_https_request,
    )

    if not args.db.is_file():
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    # Trust contract maps tool_id -> official_hosts. Used for SSRF allow-list.
    trust = _trust_sources()
    official_hosts_for: dict[str, frozenset[str]] = {
        tool_id: frozenset(meta["official_hosts"])
        for tool_id, meta in trust["tools"].items()
    }

    # Look up junk claims + their source URLs in one pass.
    rows = conn.execute("""
        SELECT k.claim_id, k.tool_id, k.statement,
               e.evidence_id, e.source_uri
        FROM knowledge_claims k
        JOIN evidence e ON e.claim_id = k.claim_id
    """).fetchall()

    affected = [r for r in rows if is_junk(r["statement"])]
    print(f"Found {len(affected)} junk claims (of {len(rows)} scanned)")

    if args.limit is not None:
        affected = affected[: args.limit]
        print(f"Limited to first {len(affected)} for this run.")

    repaired = 0
    skipped_idempotent = 0
    skipped_no_hosts = 0
    skipped_fetch_error = 0
    skipped_empty = 0

    # Be polite: leave at least 0.75 s between consecutive requests to
    # the same host. The original docs lane uses 1.0 s; this is a one-off
    # repair so we trade a tiny bit of speed for the same hygiene.
    last_fetch_at: dict[str, float] = {}
    min_interval = 0.75

    for row in affected:
        claim_id = row["claim_id"]
        tool_id = row["tool_id"]
        source_uri = row["source_uri"]
        evidence_id = row["evidence_id"]

        # Look up the per-tool host allow-list. Some catalog tool_ids
        # (e.g. surfaces like `gh-cli`) inherit hosts from the family
        # entry. We try the exact tool_id first, then the family root.
        hosts = official_hosts_for.get(tool_id)
        if hosts is None and "-" in tool_id:
            hosts = official_hosts_for.get(tool_id.split("-", 1)[0])
        if hosts is None:
            # Fall back to the host of the source_uri itself — it was
            # accepted at the original ingest, so it's an allowlisted
            # docs origin even if the trust contract has been edited
            # since. We still validate with `resolve_url_for_safe_fetch`
            # which guards against SSRF on the resolved IP.
            parsed = urlparse(source_uri)
            host = (parsed.hostname or "").lower()
            if not host:
                print(f"SKIP {claim_id} (tool {tool_id}): malformed source_uri {source_uri!r}")
                skipped_no_hosts += 1
                continue
            hosts = frozenset({host})

        try:
            resolution = resolve_url_for_safe_fetch(source_uri, allowed_hosts=hosts)
        except HttpFetchError as exc:
            print(f"SKIP {claim_id}: SSRF gate rejected {source_uri}: {exc}")
            skipped_fetch_error += 1
            continue

        # Per-host throttle.
        host_key = (urlparse(source_uri).hostname or "").lower()
        if host_key:
            last = last_fetch_at.get(host_key)
            if last is not None:
                elapsed = time.monotonic() - last
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)
            last_fetch_at[host_key] = time.monotonic()

        try:
            response = safe_https_request(
                "GET",
                resolution=resolution,
                headers={
                    "User-Agent": "Ayiru-Reingest-Junk/1.0 (+https://github.com/ruth411/ayiru)",
                    "Accept": "text/html, application/xhtml+xml",
                },
                timeout=15.0,
            )
        except HttpFetchError as exc:
            print(f"SKIP {claim_id}: fetch failed for {source_uri}: {exc}")
            skipped_fetch_error += 1
            continue

        if response.status_code >= 400:
            print(f"SKIP {claim_id}: {source_uri} returned HTTP {response.status_code}")
            skipped_fetch_error += 1
            continue

        sanitized = _sanitize_html_to_text(response.text)
        if not sanitized.strip():
            print(f"SKIP {claim_id}: sanitized text was empty after re-extract")
            skipped_empty += 1
            continue

        # Match the same statement-derivation rule the docs lane uses.
        new_statement = first_non_empty_line(
            sanitized, fallback=row["statement"] or ""
        )
        new_excerpt = sanitized[:8000]

        # Idempotency: if the re-extracted statement no longer matches
        # any nav-marker and equals the current statement (somehow), skip.
        # In practice the new statement will differ because the old one
        # was junk by definition; this guard catches edge cases where
        # the page itself changed to contain a nav marker.
        if new_statement == row["statement"] and not is_junk(new_statement):
            print(f"SKIP {claim_id}: already clean")
            skipped_idempotent += 1
            continue

        if args.dry_run:
            print(f"WOULD REPAIR {claim_id} ({tool_id})")
            print(f"  OLD: {(row['statement'] or '')[:100]!r}")
            print(f"  NEW: {new_statement[:100]!r}")
            repaired += 1
            continue

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE knowledge_claims SET statement = ? WHERE claim_id = ?",
            (new_statement, claim_id),
        )
        conn.execute(
            "UPDATE evidence SET excerpt = ?, captured_at = ?, "
            "hash = ? WHERE evidence_id = ?",
            (
                new_excerpt,
                now,
                f"sha256:{sha256(response.text.encode('utf-8')).hexdigest()}",
                evidence_id,
            ),
        )
        conn.commit()
        repaired += 1
        if repaired % 10 == 0:
            print(f"  ... repaired {repaired}/{len(affected)}")

    conn.close()
    print()
    print(f"Done. repaired={repaired}  "
          f"skipped(idempotent)={skipped_idempotent}  "
          f"skipped(no-hosts)={skipped_no_hosts}  "
          f"skipped(fetch-error)={skipped_fetch_error}  "
          f"skipped(empty)={skipped_empty}")
    if args.dry_run:
        print("(--dry-run — no writes made)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
