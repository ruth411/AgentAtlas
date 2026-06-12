"""Recompute `knowledge_claims.confidence` + `confidence_band` under the
v2 confidence model.

The 2026-06-12 audit found 99% of the catalog stuck in the `[0.3, 0.5)`
range under v1's weights. v2 raises the per-type weights for canonical
evidence (`official_docs`, `man_page`, `sandbox_execution`, …) so a
single high-trust citation reaches `moderate` (≥0.55). After this
rescore, Phase 4's orchestrator re-pass can promote those claims from
`pending` / `requires_human_review` to `accepted` where appropriate.

Conflict detection is NOT re-run here; that's the orchestrator's job in
Phase 4. We pass ``has_conflict=False`` uniformly, so existing
conflict-marked claims will have their score recomputed without the cap.
Phase 4 reapplies the cap when the orchestrator decides.

Usage:
    python tools/scripts/rescore_confidence.py --dry-run
    python tools/scripts/rescore_confidence.py
    python tools/scripts/rescore_confidence.py --limit 50  # smoke test
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _parse_dt(value: str) -> datetime:
    """Parse a stored ISO timestamp. SQLite TEXT columns often drop the
    timezone marker; the Evidence pydantic model requires tz-aware, so
    we coerce naive datetimes to UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print before/after histograms without writing.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after rescoring N claims (smoke test).")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 2

    # Lazy imports — the contract / model is parsed at first call.
    from app.schemas.claim import KnowledgeClaim
    from app.schemas.enums import ClaimType, EvidenceType, RiskLevel, TrustLevel, VerificationStatus
    from app.schemas.evidence import Evidence
    from app.services.confidence_scorer import compute_confidence_breakdown

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    # Existing distribution.
    before = Counter(
        row["confidence_band"] or "(null)"
        for row in conn.execute("SELECT confidence_band FROM knowledge_claims")
    )
    print("Before (v1 confidence_band distribution):")
    for band, n in sorted(before.items(), key=lambda kv: -kv[1]):
        print(f"  {band}: {n}")

    rows = conn.execute("""
        SELECT claim_id, claim_type, subject, statement, tool_id,
               submitted_by, risk_level, verification_status,
               created_at
        FROM knowledge_claims
    """).fetchall()
    if args.limit:
        rows = rows[: args.limit]

    after = Counter()
    updates: list[tuple[float, str, str]] = []

    for r in rows:
        # Pull evidence for the claim.
        ev_rows = conn.execute("""
            SELECT evidence_id, evidence_type, source_uri, excerpt, hash,
                   captured_at, trust_level
            FROM evidence WHERE claim_id = ?
        """, (r["claim_id"],)).fetchall()

        evidence = []
        for er in ev_rows:
            evidence.append(
                Evidence(
                    evidence_id=er["evidence_id"],
                    evidence_type=EvidenceType(er["evidence_type"]),
                    source_uri=er["source_uri"],
                    excerpt=er["excerpt"] or "(empty)",
                    hash=er["hash"],
                    captured_at=_parse_dt(er["captured_at"]),
                    trust_level=TrustLevel(er["trust_level"]),
                )
            )

        if not evidence:
            # Pre-existing rule: zero evidence → 0.0 / none band.
            new_score = 0.0
            new_band = "none"
        else:
            claim = KnowledgeClaim(
                claim_id=r["claim_id"],
                claim_type=ClaimType(r["claim_type"]),
                subject=r["subject"],
                statement=r["statement"],
                tool_id=r["tool_id"],
                submitted_by=r["submitted_by"],
                evidence=evidence,
                risk_level=RiskLevel(r["risk_level"]) if r["risk_level"] else RiskLevel.LOW,
                verification_status=VerificationStatus(r["verification_status"])
                if r["verification_status"] else VerificationStatus.PENDING,
                confidence=None,
                confidence_band=None,
                created_at=_parse_dt(r["created_at"]),
            )
            breakdown = compute_confidence_breakdown(claim, has_conflict=False)
            new_score = breakdown.score
            new_band = breakdown.band.value

        after[new_band] += 1
        updates.append((round(new_score, 4), new_band, r["claim_id"]))

    print("\nAfter (v2 projected confidence_band distribution):")
    for band, n in sorted(after.items(), key=lambda kv: -kv[1]):
        print(f"  {band}: {n}")

    if args.dry_run:
        print("\n(--dry-run — no writes)")
        return 0

    print(f"\nWriting {len(updates)} updates…")
    # Single transaction for the whole rescore.
    conn.executemany(
        "UPDATE knowledge_claims SET confidence = ?, confidence_band = ? "
        "WHERE claim_id = ?",
        updates,
    )
    conn.commit()
    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
