"""AgentAtlas command-line entry point.

`pip install agentatlas` lands a single binary named `agentatlas` on PATH
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
  to their subcommand so `agentatlas --version` and `agentatlas --help`
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


PACKAGE_VERSION = "0.1.0"


# -------- argparse wiring --------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentatlas",
        description=(
            "AgentAtlas — verified, machine-readable knowledge layer for AI agents."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agentatlas {PACKAGE_VERSION}",
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
        help="SQLAlchemy URL (defaults to $AGENTATLAS_DATABASE_URL or backend/agentatlas.db).",
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
        default="agentatlas-cli",
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


# -------- subcommand implementations --------


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _cmd_mcp(_args: argparse.Namespace) -> int:
    from app.mcp_server.server import build_default_server

    build_default_server().serve()
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    # The seed script lives outside the installed package (in `scripts/`)
    # so we load it by path. This keeps the script importable when running
    # from a checkout and avoids shipping demo data inside the wheel.
    seed_module = _load_seed_module()
    seed_argv: list[str] = []
    if args.reset:
        seed_argv.append("--reset")
    if args.database_url:
        seed_argv += ["--database-url", args.database_url]
    return int(seed_module.main(seed_argv))


def _cmd_migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    cfg_path = _alembic_ini_path()
    if cfg_path is None:
        print(
            "agentatlas migrate: could not locate alembic.ini. Run from a "
            "checkout (backend/alembic.ini) or set AGENTATLAS_ALEMBIC_INI.",
            file=sys.stderr,
        )
        return 1

    cfg = Config(str(cfg_path))
    # alembic resolves script_location relative to the ini's directory;
    # set the working CWD so its relative `script_location = alembic`
    # entry continues to work no matter where the user invoked us.
    prior_cwd = os.getcwd()
    os.chdir(cfg_path.parent)
    try:
        if args.database_url:
            cfg.set_main_option("sqlalchemy.url", args.database_url)
        command.upgrade(cfg, "head")
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
    # callers can chain `agentatlas query ... && <do thing>` safely.
    return 0 if payload["safe_to_auto_execute"] else 2


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
        print(f"agentatlas verify: {exc}", file=sys.stderr)
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
        print("(no tools published yet — try `agentatlas seed --reset`)")
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


def _load_seed_module() -> Any:
    """Import `scripts/seed_examples.py` by path.

    Walks up from this file looking for `<repo>/scripts/seed_examples.py`.
    Stops at the first `.git` directory (the repo root) so we don't keep
    climbing into the user's filesystem indefinitely. Falls back to
    `$AGENTATLAS_SEED_SCRIPT` if the wheel was installed somewhere that
    doesn't sit next to the repo tree.

    NOTE: `data/seed_artifacts/` and `scripts/seed_examples.py` are
    deliberately not packaged inside the wheel — they're demo data, not
    core functionality. `pip install agentatlas` users who want the
    seeded demo graph need a git checkout (or to set the env var to a
    seed_examples.py somewhere on disk).
    """
    override = os.environ.get("AGENTATLAS_SEED_SCRIPT")
    candidate_paths: list[Path] = []
    if override:
        candidate_paths.append(Path(override))
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate_paths.append(parent / "scripts" / "seed_examples.py")
        if (parent / ".git").exists():
            break

    for path in candidate_paths:
        if path.is_file():
            return _import_module_from_path("agentatlas_seed_examples", path)

    raise RuntimeError(
        "agentatlas seed: could not locate scripts/seed_examples.py. The "
        "demo seed data ships in the git repo, not the PyPI wheel. Either "
        "run from a checkout (https://github.com/ruth411/AgentAtlas), or "
        "set AGENTATLAS_SEED_SCRIPT to the absolute path of a "
        "seed_examples.py on disk."
    )


def _import_module_from_path(name: str, path: Path) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"agentatlas: failed to load module at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _alembic_ini_path() -> Path | None:
    override = os.environ.get("AGENTATLAS_ALEMBIC_INI")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "alembic.ini"
        if candidate.is_file():
            return candidate
        if (parent / ".git").exists():
            break
    return None


if __name__ == "__main__":
    raise SystemExit(main())
