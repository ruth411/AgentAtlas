"""Phase-5 catalog cleanup: prune surface-less stubs, promote terraform,
dedupe ansible-cli pairs.

The 2026-06-12 audit flagged three structural problems:

1. **Surface-less stubs.** Seven tool_ids hold a single placeholder
   claim with no 5-surface decomposition: ``systemctl``, ``tmux``,
   ``uv``, ``vim``, ``wget``, ``yarn``, ``rsync``. These are vestiges
   of an exploratory pass that never went deep. Delete.

2. **Bare ``terraform``** has 7 real claims (init, plan, apply, destroy,
   state, import + overview) that map cleanly onto a ``terraform-cli``
   surface — they're useful, just under the wrong id. Promote the
   tool_id from ``terraform`` to ``terraform-cli``.

3. **ansible-cli duplicates.** Four subject pairs (``ansible-config``,
   ``ansible-doc``, ``ansible-galaxy``, ``ansible-playbook``) have two
   claims each with identical subjects. Keep the higher-confidence one,
   delete the other (and its evidence + verification rows).

Usage:
    python tools/scripts/prune_stub_tools.py --dry-run
    python tools/scripts/prune_stub_tools.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"

_STUBS_TO_DELETE = ("systemctl", "tmux", "uv", "vim", "wget", "yarn", "rsync")
_ANSIBLE_DUPE_SUBJECTS = (
    "ansible-config",
    "ansible-doc",
    "ansible-galaxy",
    "ansible-playbook",
)


def _delete_claim_cascade(conn: sqlite3.Connection, claim_id: str) -> None:
    conn.execute("DELETE FROM verification_results WHERE claim_id = ?", (claim_id,))
    conn.execute("DELETE FROM evidence WHERE claim_id = ?", (claim_id,))
    conn.execute("DELETE FROM knowledge_claims WHERE claim_id = ?", (claim_id,))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    print("=" * 64)
    print("1. Prune surface-less stubs")
    print("=" * 64)
    deleted_stub_claims = 0
    for tid in _STUBS_TO_DELETE:
        ids = [r[0] for r in conn.execute(
            "SELECT claim_id FROM knowledge_claims WHERE tool_id = ?", (tid,)
        )]
        print(f"  {tid:>12}: {len(ids)} claim(s) -> delete")
        if not args.dry_run:
            for cid in ids:
                _delete_claim_cascade(conn, cid)
            deleted_stub_claims += len(ids)
    print(f"  ({deleted_stub_claims} stub claims deleted)")

    print()
    print("=" * 64)
    print("2. Promote bare `terraform` claims to `terraform-cli`")
    print("=" * 64)
    terraform_count = conn.execute(
        "SELECT COUNT(*) FROM knowledge_claims WHERE tool_id = 'terraform'"
    ).fetchone()[0]
    print(f"  Moving {terraform_count} claim(s) from `terraform` -> `terraform-cli`")
    if not args.dry_run and terraform_count:
        conn.execute(
            "UPDATE knowledge_claims SET tool_id = 'terraform-cli' "
            "WHERE tool_id = 'terraform'"
        )

    print()
    print("=" * 64)
    print("3. Dedupe ansible-cli pairs (keep higher-confidence)")
    print("=" * 64)
    deleted_dupe_claims = 0
    for subject in _ANSIBLE_DUPE_SUBJECTS:
        rows = list(conn.execute(
            "SELECT claim_id, confidence FROM knowledge_claims "
            "WHERE tool_id = 'ansible-cli' AND subject = ? "
            "ORDER BY confidence DESC NULLS LAST, claim_id",
            (subject,),
        ))
        if len(rows) <= 1:
            print(f"  {subject:>16}: only 1 claim, skip")
            continue
        keep = rows[0]
        drop = rows[1:]
        print(f"  {subject:>16}: keep {keep['claim_id'][:14]} "
              f"(conf={keep['confidence']}), drop {len(drop)}")
        if not args.dry_run:
            for d in drop:
                _delete_claim_cascade(conn, d["claim_id"])
            deleted_dupe_claims += len(drop)

    if not args.dry_run:
        conn.commit()

    # Summary.
    final_claims = conn.execute("SELECT COUNT(*) FROM knowledge_claims").fetchone()[0]
    conn.close()

    print()
    print("=" * 64)
    print("SUMMARY")
    print("=" * 64)
    if args.dry_run:
        print("  (--dry-run — no writes)")
    else:
        print(f"  Stub claims deleted:        {deleted_stub_claims}")
        print(f"  Terraform claims promoted:  {terraform_count}")
        print(f"  Duplicate claims removed:   {deleted_dupe_claims}")
        print(f"  Final claim count:          {final_claims}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
