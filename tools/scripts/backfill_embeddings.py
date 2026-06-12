"""Backfill `knowledge_claims.embedding` for rows where it's NULL.

The 2026-06-12 catalog audit found that 27.7% of claims (1,008 of 3,637)
have no stored embedding — concentrated on the CLI surfaces that take
the most natural-language queries:

    gh-cli:     95% missing      git-cli:     94% missing
    cargo-cli:  97% missing      pnpm-cli:    97% missing
    openssl-cli:96% missing      docker-cli:  81% missing
    kubectl-cli:84% missing      helm-cli:    81% missing
    go-stdlib: 100% missing      awk-language:100% missing

Embeddings are computed by `EmbeddingService.embed_many()` using
`BAAI/bge-small-en-v1.5` (384-dim, ONNX, CPU). Batching is essential —
per-call ONNX session overhead dominates; embedding 1,000 claims as
1,000 batches of 1 takes minutes, as 16 batches of 64 takes seconds.

Run AFTER `reingest_junk_claims.py` so the embeddings reflect the
cleaned statements, not the nav-menu text the original ingest captured.

Usage:
    python tools/scripts/backfill_embeddings.py --dry-run
    python tools/scripts/backfill_embeddings.py                 # full backfill
    python tools/scripts/backfill_embeddings.py --batch-size 32 # smaller batches
    python tools/scripts/backfill_embeddings.py --limit 200     # smoke test
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after embedding N claims (smoke test).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the per-surface gap and exit without computing.")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 2

    # Lazy import — fastembed pulls in ONNX + numpy, which we only need
    # for the actual embed call.
    from app.services.embedding_service import (
        EmbeddingService,
        claim_embedding_text,
        serialize_embedding,
    )

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    # The ORDER BY puts CLI surfaces first so a `--limit` smoke run hits
    # the surfaces that need it most. The CASE expression is sqlite-safe
    # (no LIKE in ORDER BY).
    rows = conn.execute("""
        SELECT claim_id, subject, statement, tool_id
        FROM knowledge_claims
        WHERE embedding IS NULL OR length(embedding) = 0
        ORDER BY
          CASE WHEN tool_id LIKE '%-cli' THEN 0 ELSE 1 END,
          tool_id, claim_id
    """).fetchall()
    print(f"Found {len(rows)} claims missing an embedding "
          f"(of {conn.execute('SELECT COUNT(*) FROM knowledge_claims').fetchone()[0]} total).")

    # Per-surface breakdown (so a dry-run report tells the user what
    # they'd be touching without computing anything).
    by_surface: dict[str, int] = {}
    for r in rows:
        by_surface[r["tool_id"]] = by_surface.get(r["tool_id"], 0) + 1
    print("\nTop-10 surfaces with missing embeddings:")
    for tool_id, n in sorted(by_surface.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {tool_id:>20}: {n}")

    if args.limit is not None:
        rows = rows[: args.limit]
        print(f"\nLimited to first {len(rows)} for this run.")

    if args.dry_run:
        print("\n(--dry-run — no embeddings computed)")
        conn.close()
        return 0

    if not rows:
        print("\nNothing to do.")
        conn.close()
        return 0

    print(f"\nEmbedding {len(rows)} claims in batches of {args.batch_size}…")
    service = EmbeddingService.get_default()

    embedded = 0
    skipped_empty = 0
    for i in range(0, len(rows), args.batch_size):
        batch = rows[i : i + args.batch_size]
        texts: list[str] = []
        target_ids: list[str] = []
        for r in batch:
            subject = (r["subject"] or "").strip()
            statement = (r["statement"] or "").strip()
            if not subject and not statement:
                # Claim has nothing to embed — leave NULL, the matcher
                # falls through to lexical-only for these.
                skipped_empty += 1
                continue
            texts.append(claim_embedding_text(subject=subject, statement=statement))
            target_ids.append(r["claim_id"])

        if not texts:
            continue

        vectors = service.embed_many(texts)
        if len(vectors) != len(target_ids):
            print(f"ERROR: vector count mismatch ({len(vectors)} vs {len(target_ids)})",
                  file=sys.stderr)
            return 1

        # Single transaction per batch — keeps wal/journal traffic bounded.
        conn.executemany(
            "UPDATE knowledge_claims SET embedding = ? WHERE claim_id = ?",
            [
                (serialize_embedding(vec), cid)
                for vec, cid in zip(vectors, target_ids)
            ],
        )
        conn.commit()
        embedded += len(texts)
        print(f"  ... {embedded}/{len(rows) - skipped_empty} embedded")

    conn.close()
    print(f"\nDone. embedded={embedded}  skipped(empty)={skipped_empty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
