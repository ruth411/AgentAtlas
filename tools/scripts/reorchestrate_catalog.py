"""Re-run the orchestrator over the catalog so previously-blocked claims
can move to `accepted` under the v2 confidence model.

The Phase 3 rescore pushed 3,589 claims into the `moderate` band (was 3
under v1). The orchestrator's acceptance gate checks confidence band +
trusted-evidence presence + conflict status; claims that were
``pending`` only because the band was ``low`` should now flip to
``accepted``.

Two modes:

- ``--dry-run`` (default off): projects the decision for every claim and
  prints a histogram. No writes.
- apply: calls ``CanonOrchestrator.verify_claim`` against every claim,
  persists the new ``VerificationResult`` via ``save_verification_result``
  (which also updates the claim row's ``verification_status``).

Conflict detection finds existing duplicates/conflicts in the store as a
side-effect; that's the orchestrator's job and we let it do its thing.

Usage:
    python tools/scripts/reorchestrate_catalog.py --dry-run
    python tools/scripts/reorchestrate_catalog.py
    python tools/scripts/reorchestrate_catalog.py --family gh
    python tools/scripts/reorchestrate_catalog.py --limit 100
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"
DEFAULT_REPORT = REPO_ROOT / "data" / f"orchestrator_pass_{datetime.now(timezone.utc).date()}.json"


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true",
                        help="Project decisions only, no writes.")
    parser.add_argument("--family", default=None,
                        help="Filter to a single tool-family prefix (e.g. gh, kubectl).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N claims (smoke test).")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                        help="Path to write the JSON audit artifact.")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 2

    # Lazy imports.
    from app.schemas.claim import KnowledgeClaim
    from app.schemas.enums import (
        ClaimType,
        EvidenceType,
        RiskLevel,
        TrustLevel,
        VerificationStatus,
    )
    from app.schemas.evidence import Evidence
    from app.services.claim_store import ClaimStore
    from app.services.orchestrator import CanonOrchestrator

    # Read the raw rows ourselves (bypassing the ORM) so the script
    # stays cheap on a 3,600-claim pass.
    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    where_clause = ""
    params: tuple = ()
    if args.family:
        where_clause = "WHERE tool_id = ? OR tool_id LIKE ?"
        params = (args.family, f"{args.family}-%")

    claim_rows = conn.execute(f"""
        SELECT claim_id, claim_type, subject, statement, tool_id,
               submitted_by, risk_level, verification_status, created_at
        FROM knowledge_claims
        {where_clause}
        ORDER BY tool_id, claim_id
    """, params).fetchall()
    if args.limit:
        claim_rows = claim_rows[: args.limit]
    conn.close()

    print(f"Processing {len(claim_rows)} claims"
          + (f" (family={args.family})" if args.family else "")
          + (f", LIMIT={args.limit}" if args.limit else ""))

    # The orchestrator needs a ClaimStore (ORM-backed). We open one in
    # read-mode for projection; the apply mode reuses the same store
    # since save_verification_result lives there.
    store = ClaimStore(database_url=f"sqlite:///{args.db}")
    orchestrator = CanonOrchestrator(store)

    before_status: Counter[str] = Counter()
    after_decision: Counter[str] = Counter()
    per_family_before: dict[str, Counter[str]] = {}
    per_family_after: dict[str, Counter[str]] = {}
    transitions: list[dict] = []
    apply_errors = 0

    for i, r in enumerate(claim_rows):
        # Pull the evidence in a separate connection so we don't reuse
        # the ORM's session.
        ev_conn = sqlite3.connect(str(args.db))
        ev_conn.row_factory = sqlite3.Row
        ev_rows = ev_conn.execute("""
            SELECT evidence_id, evidence_type, source_uri, excerpt, hash,
                   captured_at, trust_level
            FROM evidence WHERE claim_id = ?
        """, (r["claim_id"],)).fetchall()
        ev_conn.close()

        evidence = [
            Evidence(
                evidence_id=er["evidence_id"],
                evidence_type=EvidenceType(er["evidence_type"]),
                source_uri=er["source_uri"],
                excerpt=er["excerpt"] or "(empty)",
                hash=er["hash"],
                captured_at=_parse_dt(er["captured_at"]),
                trust_level=TrustLevel(er["trust_level"]),
            )
            for er in ev_rows
        ]

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

        family = claim.tool_id.split("-", 1)[0] if "-" in claim.tool_id else claim.tool_id
        before_status[r["verification_status"] or "unknown"] += 1
        per_family_before.setdefault(family, Counter())[r["verification_status"] or "unknown"] += 1

        result = orchestrator.verify_claim(claim)
        after_decision[result.decision.value] += 1
        per_family_after.setdefault(family, Counter())[result.verification_status.value] += 1

        if r["verification_status"] != result.verification_status.value:
            transitions.append({
                "claim_id": r["claim_id"],
                "tool_id": r["tool_id"],
                "subject": r["subject"][:80],
                "from": r["verification_status"],
                "to": result.verification_status.value,
                "decision": result.decision.value,
                "confidence": result.confidence,
            })

        if not args.dry_run:
            try:
                store.save_verification_result(result)
            except Exception as exc:
                apply_errors += 1
                print(f"  ERROR saving result for {r['claim_id']}: {exc}",
                      file=sys.stderr)

        if (i + 1) % 200 == 0:
            print(f"  ... processed {i + 1}/{len(claim_rows)}")

    print("\nBEFORE verification_status:")
    for k, n in sorted(before_status.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {n}")
    print("\nAFTER projected verification_status (== save target in apply mode):")
    # Collapse per-family-after into a global tally.
    global_after: Counter[str] = Counter()
    for fam_counter in per_family_after.values():
        global_after.update(fam_counter)
    for k, n in sorted(global_after.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {n}")

    print(f"\nTransitions: {len(transitions)} claims changed status.")
    family_accept_count = {
        fam: sum(n for status, n in counter.items() if status == "accepted")
        for fam, counter in per_family_after.items()
    }
    families_with_accept = sum(1 for n in family_accept_count.values() if n > 0)
    print(f"Families with ≥1 accepted claim: {families_with_accept}/{len(per_family_after)}")

    # Write the audit artifact.
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "family_filter": args.family,
        "limit": args.limit,
        "total_claims_processed": len(claim_rows),
        "before_status_histogram": dict(before_status),
        "after_status_histogram": dict(global_after),
        "families_with_accepted_claim": families_with_accept,
        "per_family_accepted": family_accept_count,
        "transitions_count": len(transitions),
        "transitions_sample": transitions[:50],
        "apply_errors": apply_errors,
    }, indent=2, default=str))
    print(f"\nReport: {args.report}")
    if args.dry_run:
        print("(--dry-run — no writes made)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
