"""Ayiru command-line entry point.

`pip install ayiru` lands a single binary named `ayiru` on PATH
that dispatches to this module's `main()`. Subcommands wrap the same
service objects the REST and MCP layers use — the CLI is a thin shell,
not a parallel implementation.

Subcommands:

- `serve`            — run the FastAPI app under uvicorn
- `mcp`              — speak MCP/JSON-RPC over stdio (for Claude Desktop)
- `seed`             — replay `data/seed_artifacts/` into the local DB
- `migrate`          — `alembic upgrade head`
- `query`            — call `validate_command` and pretty-print the verdict
- `verify`           — promote a claim to L3 via the runtime verifier
- `tools`            — list every tool currently in the canonical graph
- `--version`        — print the package version

Design notes:

- argparse only — no `click`/`typer` dependency.
- Every subcommand returns an `int` exit code. `main()` returns the same
  code so `sys.exit(main())` does the right thing.
- Heavy imports (`uvicorn`, `alembic`, the orchestrator stack) are local
  to their subcommand so `ayiru --version` and `ayiru --help`
  start cold in a few hundred ms instead of pulling FastAPI's entire
  graph up front.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _package_version() -> str:
    """Read the installed wheel's version metadata at runtime so the
    CLI's `--version` output stays in sync with pyproject.toml without
    a hardcoded duplicate. Earlier draft set ``PACKAGE_VERSION = "0.1.0"``
    in this module and the audit caught the drift hazard: bump pyproject
    to 0.2.0 and the CLI silently keeps saying 0.1.0.

    Falls back to "0.0.0+unknown" only if the package isn't installed at
    all (i.e., running the source tree directly without `pip install -e`).
    """
    try:
        from importlib.metadata import version

        return version("ayiru")
    except Exception:
        # importlib.metadata.PackageNotFoundError is the expected miss;
        # any other failure (very old Python without importlib.metadata)
        # falls through to the same sentinel.
        return "0.0.0+unknown"


PACKAGE_VERSION = _package_version()


# -------- argparse wiring --------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ayiru",
        description=(
            "Ayiru — verified, machine-readable knowledge layer for AI agents."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ayiru {PACKAGE_VERSION}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # serve
    p_serve = sub.add_parser(
        "serve", help="Run the FastAPI app under uvicorn."
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn autoreload (development only).",
    )
    p_serve.add_argument(
        "--no-migrate",
        action="store_true",
        help=(
            "Skip the idempotent `alembic upgrade head` that runs before "
            "the server starts. Useful in production deployments that "
            "manage migrations out of band."
        ),
    )
    p_serve.add_argument(
        "--auto-seed",
        action="store_true",
        help=(
            "If the database has zero claims after migration, run "
            "`seed --reset` automatically. Off by default in dev; the "
            "Docker image enables this so `docker run … ayiru` produces "
            "a populated graph on the first start."
        ),
    )
    p_serve.set_defaults(func=_cmd_serve)

    # mcp
    p_mcp = sub.add_parser(
        "mcp",
        help="Speak MCP/JSON-RPC over stdio (for Claude Desktop, IDE bridges, etc.).",
    )
    p_mcp.set_defaults(func=_cmd_mcp)

    # seed
    p_seed = sub.add_parser(
        "seed", help="Replay data/seed_artifacts into the local DB."
    )
    p_seed.add_argument(
        "--reset",
        action="store_true",
        help="Drop the DB and re-run all migrations before seeding.",
    )
    p_seed.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL (defaults to $AYIRU_DATABASE_URL or backend/ayiru.db).",
    )
    p_seed.set_defaults(func=_cmd_seed)

    # migrate
    p_migrate = sub.add_parser(
        "migrate", help="Run `alembic upgrade head` against the configured DB."
    )
    p_migrate.add_argument(
        "--database-url",
        default=None,
        help="Override sqlalchemy.url for this run.",
    )
    p_migrate.set_defaults(func=_cmd_migrate)

    # query
    p_query = sub.add_parser(
        "query",
        help=(
            "Ask the query engine whether a command is safe to run "
            "(same answer as POST /query/validate-command)."
        ),
    )
    p_query.add_argument("--tool", required=True, help="Tool id, e.g. github-cli.")
    p_query.add_argument(
        "--command",
        required=True,
        help='Command string, e.g. "gh repo delete my-org/x --yes".',
    )
    p_query.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON instead of the human-readable verdict.",
    )
    p_query.set_defaults(func=_cmd_query)

    # ask  — Stage 17 v0.2 headline
    p_ask = sub.add_parser(
        "ask",
        help=(
            "Look up a verified, cited answer from the local knowledge "
            "graph (same answer as POST /v1/query/ask). The v0.2 "
            "headline endpoint — agents should hit this before web_search."
        ),
    )
    p_ask.add_argument(
        "question",
        help='Natural-language question, e.g. "how do I delete a docker container".',
    )
    p_ask.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max answers to return (1..20, default 5).",
    )
    p_ask.add_argument(
        "--tool",
        default=None,
        help="Optional tool_id hint, e.g. 'docker' or 'git'. Narrows the search.",
    )
    p_ask.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON instead of the human-readable answer list.",
    )
    p_ask.set_defaults(func=_cmd_ask)

    # verify
    p_verify = sub.add_parser(
        "verify",
        help=(
            "Run the runtime verifier against a claim "
            "(promotes L2 → L3 when the runtime check passes)."
        ),
    )
    p_verify.add_argument("--claim-id", required=True)
    p_verify.add_argument(
        "--submitted-by",
        default="ayiru-cli",
        help="Recorded as the verification result's submitter.",
    )
    p_verify.set_defaults(func=_cmd_verify)

    # tools
    p_tools = sub.add_parser(
        "tools", help="List every tool currently published in the canonical graph."
    )
    p_tools.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON instead of a table.",
    )
    p_tools.set_defaults(func=_cmd_tools)

    # ingest — Stage 20 bulk-ingest harness. Reads a JSON tool list and
    # drives the docs ingestion lane for every (tool, url) pair. Stage
    # 19's relaxed gate lets unknown tools persist at L0; this is what
    # finally fills the graph beyond the original 5 curated tools.
    p_ingest = sub.add_parser(
        "ingest",
        help=(
            "Bulk-ingest docs for the tools listed in a JSON tool list. "
            "Each (tool_id, url) pair runs through DocsIngestionService."
        ),
    )
    p_ingest.add_argument(
        "--source",
        choices=["docs"],
        default="docs",
        help="Ingestion lane (only 'docs' supported in v0.2; OpenAPI / GraphQL / MCP "
        "remain one-off-per-endpoint surfaces).",
    )
    p_ingest.add_argument(
        "--tool-list",
        required=True,
        help="Path to a JSON file listing tools and their docs URLs. "
        "See tools/v0.2_seed.json for the canonical example.",
    )
    p_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the tool list and print what WOULD be ingested without "
        "calling the network.",
    )
    p_ingest.add_argument(
        "--resume",
        action="store_true",
        help="Skip URLs that already have a COMPLETED ingestion run in the "
        "store. Useful for re-running after partial failures without "
        "re-fetching what already succeeded. Mutually exclusive with --force.",
    )
    p_ingest.add_argument(
        "--force",
        action="store_true",
        help="Ignore --resume and re-process every URL even if it has a "
        "prior COMPLETED ingestion run. Mutually exclusive with --resume.",
    )
    p_ingest.add_argument(
        "--limit-tools",
        type=int,
        default=None,
        help="Only ingest the first N tools (useful for smoke-testing against a "
        "subset of the seed list).",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    # reindex — Stage 22-stretch: backfill embeddings for claims that
    # don't have one yet. Run once after migration 0017 lands; idempotent
    # so re-running on an already-embedded DB is a fast no-op.
    p_reindex = sub.add_parser(
        "reindex",
        help=(
            "Compute and store semantic embeddings for every claim that "
            "doesn't have one yet. Required to enable the query engine's "
            "semantic re-ranking; without it, `ask` falls back to "
            "lexical-only scoring (the v0.2 behaviour)."
        ),
    )
    p_reindex.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Embed this many claims per fastembed batch. Higher = faster "
        "throughput but more RAM. Default 64.",
    )
    p_reindex.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only embed the first N un-embedded claims. Useful for "
        "incremental backfills on large graphs.",
    )
    p_reindex.set_defaults(func=_cmd_reindex)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


# -------- subcommand implementations --------


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    # Stage 14: run `alembic upgrade head` before booting so a fresh
    # install with an empty DB doesn't fail on the first write. Alembic
    # is idempotent — already-applied migrations are skipped. Operators
    # who manage migrations out of band pass `--no-migrate`.
    if not getattr(args, "no_migrate", False):
        try:
            _auto_migrate()
        except Exception as exc:  # pragma: no cover - defensive
            print(
                f"ayiru serve: auto-migration failed ({exc}). "
                "Run `ayiru migrate` manually, fix the migration error, "
                "or pass --no-migrate if you manage schema changes out of band.",
                file=sys.stderr,
            )
            return 1

    # Stage 15.5: when --auto-seed is passed and the DB has zero claims,
    # replay the bundled seed artifacts so the headline demo works on
    # the first request. Docker's CMD sets this; dev workflow leaves it
    # off because re-seeding mid-loop would clobber local state.
    if getattr(args, "auto_seed", False):
        _maybe_auto_seed()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _maybe_auto_seed() -> None:
    """Replay the seed artifacts iff the configured DB has zero claims.

    Idempotent: if claims exist, the function is a no-op (and prints
    one line so an operator who flipped --auto-seed on by mistake knows
    why nothing changed). Honors AYIRU_DATABASE_URL the same way the
    seed runner does.
    """
    from app.db.models import KnowledgeClaimRecord
    from app.db.session import SessionLocal
    from app.seed_data.runner import main as seed_main

    try:
        with SessionLocal() as db:
            existing = db.query(KnowledgeClaimRecord).count()
    except Exception as exc:  # pragma: no cover - defensive
        # The most common cause is a missing `knowledge_claims` table —
        # which only happens when the operator passed `--no-migrate`
        # alongside `--auto-seed`. Surface the actionable fix rather
        # than the raw SQLAlchemy traceback.
        print(
            f"ayiru serve: --auto-seed skipped, could not read claim count ({exc}). "
            "If you passed --no-migrate, run `ayiru migrate` first or drop "
            "--no-migrate so auto-seed has a schema to write into.",
            file=sys.stderr,
        )
        return

    if existing > 0:
        print(
            f"ayiru serve: --auto-seed no-op (database already holds {existing} claim(s)).",
            file=sys.stderr,
        )
        return

    print(
        "ayiru serve: --auto-seed populating empty database from bundled artifacts...",
        file=sys.stderr,
    )
    try:
        seed_main([])
    except SystemExit as exc:
        # The runner uses argparse and raises SystemExit on bad input,
        # not on success. We pass an empty argv so this path is
        # defensive only.
        if exc.code not in (0, None):  # pragma: no cover - defensive
            print(
                f"ayiru serve: --auto-seed failed (exit {exc.code}); the server will "
                "start with an empty graph.",
                file=sys.stderr,
            )
    except Exception as exc:  # pragma: no cover - defensive
        print(
            f"ayiru serve: --auto-seed failed ({exc}); the server will start with "
            "an empty graph.",
            file=sys.stderr,
        )


def _auto_migrate() -> None:
    """Apply pending alembic migrations against the configured DB.

    Works from a checkout (source-tree ini), a wheel install (bundled
    migrations + programmatic config), and a custom layout
    (`AYIRU_ALEMBIC_INI`). Idempotent — re-running against an
    up-to-date DB is a no-op.
    """
    from alembic import command

    from app.services.alembic_config import make_alembic_config

    prior_cwd = os.getcwd()
    try:
        cfg = make_alembic_config()
        command.upgrade(cfg, "head")
    finally:
        os.chdir(prior_cwd)


def _cmd_mcp(_args: argparse.Namespace) -> int:
    from app.mcp_server.server import (
        build_default_server,
        configured_mcp_shared_secret,
    )

    # Stage 15.8 / 15.10 — disclose the auth asymmetry only when the
    # operator set AYIRU_API_KEY but left MCP stdio ungated. With
    # AYIRU_MCP_SHARED_SECRET configured, the stdio path requires the
    # caller to present `params.ayiru_shared_secret` in initialize.
    if (
        os.environ.get("AYIRU_API_KEY", "").strip()
        and configured_mcp_shared_secret() is None
    ):
        sys.stderr.write(
            "WARNING: AYIRU_API_KEY is set, but it does not gate the MCP "
            "stdio path unless AYIRU_MCP_SHARED_SECRET is also configured. "
            "Without that secret, the stdio JSON-RPC server accepts all "
            "framed requests, including writes (submit_claim). Ensure only "
            "trusted local callers can reach this process, or set "
            "AYIRU_MCP_SHARED_SECRET. See SECURITY.md §Known residual risks.\n"
        )
        sys.stderr.flush()
    build_default_server().serve()
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Stage 20 — bulk-ingest the docs lane for every (tool_id, url) pair
    declared in the tool-list JSON file.

    `--resume` skips URLs that already have a COMPLETED ingestion run; a
    re-run after partial failure won't re-fetch what already succeeded.
    `--force` ignores --resume so the operator can re-process the full
    set. The two flags are mutually exclusive.

    Exit codes:
      0 — every (tool, url) pair succeeded (or --dry-run completed)
      1 — at least one pair failed; counts surface in the summary
      2 — fatal setup error (bad tool list path, malformed JSON, etc.)
    """
    from app.services.claim_store import get_claim_store
    from app.services.docs_ingestion import (
        DocsIngestionError,
        DocsIngestionService,
    )

    if args.resume and args.force:
        print(
            "ayiru ingest: --resume and --force are mutually exclusive.",
            file=sys.stderr,
        )
        return 2

    tool_list_path = Path(args.tool_list)
    if not tool_list_path.is_file():
        print(
            f"ayiru ingest: tool list file not found: {tool_list_path}",
            file=sys.stderr,
        )
        return 2

    try:
        tool_list = json.loads(tool_list_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"ayiru ingest: invalid JSON in {tool_list_path}: {exc}",
            file=sys.stderr,
        )
        return 2

    # Schema validation — keep it strict so a typo doesn't silently
    # produce a no-op crawl.
    if not isinstance(tool_list, dict) or "tools" not in tool_list:
        print(
            "ayiru ingest: tool list must be a JSON object with a 'tools' array. "
            "See tools/v0.2_seed.json for the canonical schema.",
            file=sys.stderr,
        )
        return 2

    tools: list[dict[str, Any]] = list(tool_list.get("tools", []))
    if args.limit_tools is not None:
        tools = tools[: args.limit_tools]

    defaults: dict[str, Any] = tool_list.get("defaults", {})
    submitted_by = defaults.get("submitted_by", "ayiru-bulk-ingest")
    verify = bool(defaults.get("verify", True))

    print(
        f"ayiru ingest ({args.source} lane): "
        f"{len(tools)} tools in '{tool_list_path}'  "
        f"submitted_by={submitted_by!r}  verify={verify}  "
        f"dry_run={args.dry_run}  resume={args.resume}  force={args.force}"
    )

    # Tally summary across the whole run so the operator gets one
    # actionable number at the end.
    total_urls = 0
    success = 0
    failure = 0
    skipped = 0

    if args.dry_run:
        for tool in tools:
            tool_id = tool.get("tool_id", "?")
            urls = tool.get("urls", [])
            total_urls += len(urls)
            print(f"  [DRY RUN] {tool_id}: {len(urls)} URL(s)")
            for url in urls:
                print(f"    → {url}")
        print(
            f"\nDry run complete. Would ingest {total_urls} URLs across "
            f"{len(tools)} tools."
        )
        return 0

    store = get_claim_store()
    service = DocsIngestionService(store)

    for i, tool in enumerate(tools, start=1):
        tool_id = tool.get("tool_id")
        urls = tool.get("urls", [])
        if not tool_id or not urls:
            print(
                f"  [{i}/{len(tools)}] ⚠  entry missing tool_id or urls; "
                f"skipping: {tool!r}"
            )
            skipped += len(urls or [1])
            continue
        print(f"  [{i}/{len(tools)}] {tool_id} ({len(urls)} URL(s))")
        for url in urls:
            total_urls += 1
            if args.resume and not args.force and store.has_completed_ingestion_run(
                tool_id=tool_id, command=url
            ):
                skipped += 1
                print(f"    ↷ {url}  (resume: prior COMPLETED run, skipped)")
                continue
            try:
                response = service.ingest(
                    tool_id=tool_id,
                    url=url,
                    submitted_by=submitted_by,
                    verify=verify,
                )
                # The ingestion service returns a structured response with
                # a status enum and claim ids; print the headline numbers.
                status = response.status.value
                claim_count = len(response.created_claim_ids)
                if status == "completed":
                    success += 1
                    print(f"    ✓ {url}  ({status}, +{claim_count} claims)")
                else:
                    failure += 1
                    print(
                        f"    ✗ {url}  ({status}; see ingestion run "
                        f"{response.run_id!r})"
                    )
            except DocsIngestionError as exc:
                failure += 1
                print(f"    ✗ {url}  (DocsIngestionError: {exc})")
            except Exception as exc:  # noqa: BLE001
                failure += 1
                print(f"    ✗ {url}  ({type(exc).__name__}: {exc})")

    print(
        f"\nIngest complete: {success} ok / {failure} failed / "
        f"{skipped} skipped across {total_urls} URLs."
    )
    return 0 if failure == 0 else 1


def _cmd_reindex(args: argparse.Namespace) -> int:
    """Stage 22-stretch — backfill semantic embeddings for un-embedded claims.

    Computes a 384-dim BGE embedding of ``subject + statement`` for every
    claim whose ``embedding`` column is NULL, then writes it via
    ``ClaimStore.set_embedding``. Idempotent: re-running after every claim
    has an embedding is a fast no-op (0 claims to process).

    Exit codes:
      0 — success (including the no-op case)
      1 — at least one claim failed to embed; counts surface in the summary
    """
    from app.services.claim_store import get_claim_store
    from app.services.embedding_service import (
        EmbeddingService,
        claim_embedding_text,
        serialize_embedding,
    )

    store = get_claim_store()
    todo_ids = store.list_unembedded_claim_ids(
        limit=args.limit if args.limit is not None else 1_000_000
    )
    total = len(todo_ids)

    if total == 0:
        print("ayiru reindex: no un-embedded claims; graph is up to date.")
        return 0

    print(
        f"ayiru reindex: {total} claims to embed "
        f"(batch_size={args.batch_size})  this loads BAAI/bge-small-en-v1.5 "
        f"on first call (~130 MB, one-time)…",
        flush=True,
    )

    svc = EmbeddingService.get_default()
    ok = failed = 0

    # Process in batches: fastembed amortizes ONNX session overhead, so
    # batching is ~5x faster than one-by-one even for small graphs.
    for batch_start in range(0, total, args.batch_size):
        batch_ids = todo_ids[batch_start : batch_start + args.batch_size]
        claims = [store.get(cid) for cid in batch_ids]
        valid = [(cid, c) for cid, c in zip(batch_ids, claims) if c is not None]
        if not valid:
            failed += len(batch_ids)
            continue
        texts = [
            claim_embedding_text(subject=c.subject, statement=c.statement)
            for _, c in valid
        ]
        try:
            vectors = svc.embed_many(texts)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ batch starting at #{batch_start}: {type(exc).__name__}: {exc}")
            failed += len(valid)
            continue
        for (cid, _), vec in zip(valid, vectors):
            if store.set_embedding(cid, serialize_embedding(vec)):
                ok += 1
            else:
                failed += 1
        # Per-batch progress so a slow first-batch (cold model load) is
        # visible instead of looking hung.
        print(f"  ✓ embedded {batch_start + len(valid)}/{total}", flush=True)

    print(f"\nReindex complete: {ok} embedded / {failed} failed.")
    return 0 if failed == 0 else 1


def _cmd_seed(args: argparse.Namespace) -> int:
    # Stage 14: the seed logic moved into `app.seed_data.runner`, which
    # ships inside the wheel along with the artifacts themselves. The
    # `AYIRU_SEED_SCRIPT` env-var override still works for users who
    # want to point at a fork of `scripts/seed_examples.py`.
    override = os.environ.get("AYIRU_SEED_SCRIPT")
    seed_argv: list[str] = []
    if args.reset:
        seed_argv.append("--reset")
    if args.database_url:
        seed_argv += ["--database-url", args.database_url]
    if override:
        seed_module = _import_module_from_path(
            "ayiru_seed_examples_override", Path(override)
        )
        return int(seed_module.main(seed_argv))
    from app.seed_data.runner import main as seed_main

    return int(seed_main(seed_argv))


def _cmd_migrate(args: argparse.Namespace) -> int:
    from alembic import command

    from app.services.alembic_config import make_alembic_config

    prior_cwd = os.getcwd()
    try:
        cfg = make_alembic_config(args.database_url)
        command.upgrade(cfg, "head")
    except RuntimeError as exc:
        print(f"ayiru migrate: {exc}", file=sys.stderr)
        return 1
    finally:
        os.chdir(prior_cwd)
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    from app.services.claim_store import get_claim_store
    from app.services.query_engine import QueryEngine

    engine = QueryEngine(get_claim_store())
    verdict = engine.validate_command(tool_id=args.tool, command=args.command)
    payload = verdict.model_dump(mode="json")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    _print_verdict(payload)
    # Exit non-zero when the engine says "do not auto-execute" so shell
    # callers can chain `ayiru query ... && <do thing>` safely.
    return 0 if payload["safe_to_auto_execute"] else 2


def _cmd_ask(args: argparse.Namespace) -> int:
    """Stage 17 — `ayiru ask "question"`. Thin CLI over POST /v1/query/ask.

    Exits 0 on a hit (at least one answer returned, fallback NOT
    recommended) and 1 on a fallback so shell pipelines can branch.
    Distinct from `ayiru query`'s 0/2 contract — `query` is a safety
    gate; `ask` is a retrieval probe. Conflating them would mislead
    a script that pipes `ayiru ask ... || curl web-search-fallback`.
    """
    from app.services.claim_store import get_claim_store
    from app.services.query_engine import QueryEngine

    engine = QueryEngine(get_claim_store())
    response = engine.ask(
        question=args.question,
        limit=args.limit,
        tool_id_hint=args.tool,
    )
    payload = response.model_dump(mode="json")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if payload["fallback_recommended"] else 0

    _print_ask_response(payload)
    return 1 if payload["fallback_recommended"] else 0


def _print_ask_response(payload: dict[str, Any]) -> None:
    """Human-readable rendering of an AskResponse for the CLI."""
    if payload["fallback_recommended"]:
        print("FALLBACK — no confident match in the local graph.")
        print("  → escalate to web_search.")
        return

    print(
        f"HIT  status={payload['answer_status']}  answers={len(payload['answers'])}  "
        f"estimated_tokens_saved={payload['estimated_tokens_saved']}"
    )
    for i, answer in enumerate(payload["answers"], start=1):
        print(
            f"  {i}. [{answer['tool_id']}] {answer['subject']} "
            f"(conf={answer['confidence']:.2f} {answer['confidence_band']}, "
            f"{answer['verification_status']}, {answer['verification_level']})"
        )
        # Truncate long statements so the CLI output stays scannable.
        statement = answer["statement"]
        if len(statement) > 200:
            statement = statement[:197] + "..."
        print(f"     {statement}")
        if answer["evidence"]:
            top = answer["evidence"][0]
            print(f"     ↪ {top['evidence_type']} ({top['trust_level']}): {top['source_uri']}")
        print(f"     ↪ match: {answer['match_reason']}")


def _cmd_verify(args: argparse.Namespace) -> int:
    from app.services.claim_store import get_claim_store
    from app.services.runtime_verifier import (
        RuntimeVerificationError,
        RuntimeVerificationService,
    )

    service = RuntimeVerificationService(get_claim_store())
    try:
        response = service.verify(
            claim_id=args.claim_id, submitted_by=args.submitted_by
        )
    except RuntimeVerificationError as exc:
        print(f"ayiru verify: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0 if response.runtime_check_passed or response.skipped else 2


def _cmd_tools(args: argparse.Namespace) -> int:
    from app.services.claim_store import get_claim_store
    from app.services.query_engine import QueryEngine

    engine = QueryEngine(get_claim_store())
    # `search_tools` with an empty query returns every published ToolSpec
    # ordered by tool_id; that's the listing surface we want.
    response = engine.search_tools(query="", limit=100, offset=0)
    payload = response.model_dump(mode="json")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    matches = payload.get("matches", [])
    if not matches:
        print("(no tools published yet — try `ayiru seed --reset`)")
        return 0
    width = max(len(t["tool_id"]) for t in matches)
    for tool in matches:
        name = tool.get("name") or tool["tool_id"]
        level = tool.get("verification_level", "")
        print(f"{tool['tool_id']:<{width}}  {level:<24}  {name}")
    return 0


# -------- helpers --------


def _print_verdict(payload: dict[str, Any]) -> None:
    safe = payload["safe_to_auto_execute"]
    risk = payload["risk_level"] or "unknown"
    verdict = "ALLOW" if safe else "BLOCK"
    print(f"{verdict}  risk={risk}  confidence={payload['confidence']:.2f}")
    if payload.get("matched_claim_id"):
        print(f"  matched_claim={payload['matched_claim_id']}")
    if payload.get("verification_level"):
        print(f"  verification_level={payload['verification_level']}")
    for reason in payload.get("reasons", []):
        print(f"  - {reason}")


def _import_module_from_path(name: str, path: Path) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"ayiru: failed to load module at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(main())
