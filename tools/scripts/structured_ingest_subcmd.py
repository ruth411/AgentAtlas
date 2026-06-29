"""Structured ingest of subcommand CLIs whose per-command help is a two-column
option list (brew, vercel, ...).

Both tools list subcommands one way and then print each subcommand's options in
the same ``  -s, --long <meta>   description`` two-column form the flat parser
already understands. This walker enumerates the subcommands, runs each
``<tool> <cmd> --help``, reuses ``structured_ingest_flatcli._parse_flags`` for
the option block, and persists one runtime-verified subject per subcommand.

Guarantees match the other ingesters: every flag traces to a parsed line,
unparseable option lines are counted, caps are L3, effect safety is L2.

Usage:
    structured_ingest_subcmd.py --tool brew   --database sqlite:///backend/ayiru_v0.2_bulk.db [--dry-run]
    structured_ingest_subcmd.py --tool vercel --database sqlite:///backend/ayiru_v0.2_bulk.db
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "scripts"))

from app.schemas.enums import (  # noqa: E402
    ConfidenceBand,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.services.structured_cli_ingestion import _stable_row_id, _validated_detail  # noqa: E402
from app.services.structured_knowledge_store import (  # noqa: E402
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)
import structured_ingest_flatcli as flat  # noqa: E402

_FORBIDDEN = ("|", ";", "&", ">", "<", "$(", "`", "\n", "\r")


def _run(argv: list[str]) -> str:
    for tok in argv:
        if any(f in tok for f in _FORBIDDEN):
            raise RuntimeError(f"unsafe token: {tok!r}")
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- brew --------
def _brew_subcommands() -> list[str]:
    out = _run(["brew", "commands"])
    cmds: list[str] = []
    section = None
    for line in out.splitlines():
        if line.startswith("==>"):
            section = line
            continue
        if section and "Built-in commands" in section and "developer" not in section:
            tok = line.strip()
            if re.fullmatch(r"[a-z][a-z0-9-]*", tok):
                cmds.append(tok)
    return sorted(set(cmds))


_BREW_DESTRUCTIVE = {"uninstall", "remove", "rm", "cleanup", "prune", "untap", "autoremove"}
_BREW_MUTATE_NET = {"install", "upgrade", "reinstall", "update", "fetch", "tap", "pin",
                    "unpin", "link", "unlink", "bump", "bundle"}
_BREW_NET_READ = {"search", "info", "livecheck", "home", "outdated"}


def _brew_effect(cmd: str) -> tuple[str, bool, bool, bool, bool, bool, str]:
    c = f"brew {cmd}"
    if cmd in _BREW_DESTRUCTIVE:
        return ("destructive", True, True, False, False, False,
                f"`{c}` removes installed packages or files from the local Homebrew prefix.")
    if cmd in _BREW_MUTATE_NET:
        return ("mutation", False, True, False, False, False,
                f"`{c}` downloads and/or changes locally installed Homebrew packages.")
    if cmd in _BREW_NET_READ:
        return ("network", False, True, False, False, False,
                f"`{c}` queries Homebrew package metadata over the network.")
    return ("filesystem", False, True, False, False, False,
            f"`{c}` inspects local Homebrew state without changing installed packages.")


# -------------------------------------------------------------- vercel --------
def _vercel_subcommands() -> list[str]:
    out = _run(["vercel", "--help"])
    cmds: list[str] = []
    in_cmds = False
    for line in out.splitlines():
        if re.match(r"^\s*Commands:\s*$", line):
            in_cmds = True
            continue
        if not in_cmds:
            continue
        # Command rows are deeply indented: "      deploy   [path]   desc" or
        # "      ls | list   [app]   desc". Group headers ("Basic") have no desc.
        m = re.match(r"^\s{6,}(?:[\w-]+\s*\|\s*)?([\w-]+)\s{2,}\S", line)
        if m:
            cmds.append(m.group(1))
    return sorted(set(cmds))


_VERCEL_DESTRUCTIVE = {"remove", "rm"}
_VERCEL_SECRET = {"login", "logout", "switch"}
_VERCEL_READ = {"list", "ls", "inspect", "whoami", "teams", "help", "open",
                "bisect", "dev", "build", "telemetry"}


def _vercel_effect(cmd: str) -> tuple[str, bool, bool, bool, bool, bool, str]:
    c = f"vercel {cmd}"
    if cmd in _VERCEL_DESTRUCTIVE:
        return ("destructive", True, False, True, False, False,
                f"`{c}` deletes a remote Vercel resource.")
    if cmd in _VERCEL_SECRET:
        return ("secret_exposure", False, True, False, False, True,
                f"`{c}` handles Vercel authentication credentials/tokens.")
    if cmd in _VERCEL_READ:
        return ("network", False, True, False, False, False,
                f"`{c}` reads Vercel/project state (locally or over the network).")
    return ("mutation", False, True, True, False, False,
            f"`{c}` changes remote Vercel/project state.")


# ---------------------------------------------------------------- cargo -------
def _cargo_subcommands() -> list[str]:
    out = _run(["cargo", "--list"])
    cmds: list[str] = []
    for line in out.splitlines():
        m = re.match(r"^\s{2,}([a-z][a-z0-9-]+)\s{2,}(\S.*)$", line)
        if m and not m.group(2).startswith("alias:"):
            cmds.append(m.group(1))
    return sorted(set(cmds))


_CARGO_DESTRUCTIVE = {"remove", "rm", "clean", "uninstall", "yank"}
_CARGO_NET_MUT = {"add", "update", "install", "fetch", "publish", "package", "generate-lockfile"}


def _cargo_effect(cmd: str):
    c = f"cargo {cmd}"
    if cmd in _CARGO_DESTRUCTIVE:
        return ("destructive", True, True, False, False, False,
                f"`{c}` removes dependencies, build artifacts, or published versions.")
    if cmd in _CARGO_NET_MUT:
        return ("mutation", False, True, cmd in {"publish", "yank"}, False, False,
                f"`{c}` fetches crates over the network and/or changes the manifest/registry.")
    return ("filesystem", False, True, False, False, False,
            f"`{c}` compiles or inspects the local package without network mutation.")


# ---------------------------------------------------------------- poetry ------
def _poetry_subcommands() -> list[str]:
    out = _run(["poetry", "list"])
    cmds: list[str] = []
    grab = False
    for line in out.splitlines():
        if re.match(r"^Available commands:", line.strip()):
            grab = True
            continue
        if grab:
            m = re.match(r"^\s{2}([a-z][a-z0-9-]+)\s{2,}\S", line)
            if m:
                cmds.append(m.group(1))
    return sorted(set(cmds))


_POETRY_DESTRUCTIVE = {"remove", "cache"}
_POETRY_NET_MUT = {"add", "install", "update", "lock", "sync", "publish", "self"}


def _poetry_effect(cmd: str):
    c = f"poetry {cmd}"
    if cmd == "remove":
        return ("destructive", True, True, False, False, False,
                f"`{c}` removes packages from the project.")
    if cmd in _POETRY_NET_MUT:
        return ("mutation", False, True, cmd == "publish", False, False,
                f"`{c}` fetches packages over the network and/or changes the environment.")
    if cmd == "build":
        return ("filesystem", False, True, False, False, False,
                f"`{c}` builds local distribution artifacts.")
    return ("network", False, True, False, False, False,
            f"`{c}` reads project/package state.")


# ----------------------------------------------------------------- pnpm -------
def _pnpm_subcommands() -> list[str]:
    out = _run(["pnpm", "--help"])
    cmds: list[str] = []
    for line in out.splitlines():
        if line.strip().startswith("pnpm "):
            continue
        m = re.match(r"^\s{2,}(?:[a-z]+,\s+)?([a-z][a-z0-9-]+)\s{2,}\S", line)
        if m:
            cmds.append(m.group(1))
    return sorted(set(cmds))


_PNPM_DESTRUCTIVE = {"remove", "rm", "uninstall", "un", "prune"}
_PNPM_NET_MUT = {"add", "install", "i", "update", "up", "import", "dlx", "create", "patch"}


def _pnpm_effect(cmd: str):
    c = f"pnpm {cmd}"
    if cmd in _PNPM_DESTRUCTIVE:
        return ("destructive", True, True, False, False, False,
                f"`{c}` removes installed packages from the project.")
    if cmd in _PNPM_NET_MUT:
        return ("mutation", False, True, cmd in {"publish"}, False, False,
                f"`{c}` fetches packages over the network and/or changes node_modules.")
    return ("filesystem", False, True, False, False, False,
            f"`{c}` runs scripts or inspects the local project.")


# -------------------------------------------------------------- supabase ------
def _supabase_subcommands() -> list[str]:
    out = _run(["supabase", "--help"])
    cmds: list[str] = []
    for line in out.splitlines():
        m = re.match(r"^\s{2}([a-z][a-z0-9-]+)(?:,\s*[a-z-]+)?\s+\S", line)
        if m and m.group(1) not in ("completion",):
            cmds.append(m.group(1))
    return sorted(set(cmds))


_SUPABASE_DESTRUCTIVE = {"unlink", "stop", "delete"}
_SUPABASE_SECRET = {"login", "logout", "secrets"}
_SUPABASE_READ = {"status", "inspect", "services", "test", "completion", "help"}


def _supabase_effect(cmd: str):
    c = f"supabase {cmd}"
    if cmd in _SUPABASE_DESTRUCTIVE:
        return ("destructive", True, True, cmd != "stop", False, False,
                f"`{c}` tears down or unlinks a Supabase resource.")
    if cmd in _SUPABASE_SECRET:
        return ("secret_exposure", False, True, False, False, True,
                f"`{c}` handles Supabase access tokens or secrets.")
    if cmd in _SUPABASE_READ:
        return ("network", False, True, False, False, False,
                f"`{c}` reads Supabase project/service state.")
    return ("mutation", False, True, True, False, False,
            f"`{c}` changes local or remote Supabase project state.")


# --------------------------------------------------------------- shared -------
_TOOLS = {
    "brew": {
        "family": "brew",
        "list": _brew_subcommands,
        "help_argv": lambda cmd: ["brew", cmd, "--help"],
        "effect": _brew_effect,
        "source_url": lambda cmd: f"https://docs.brew.sh/Manpage#{cmd}",
    },
    "vercel": {
        "family": "vercel",
        "list": _vercel_subcommands,
        "help_argv": lambda cmd: ["vercel", cmd, "--help"],
        "effect": _vercel_effect,
        "source_url": lambda cmd: f"https://vercel.com/docs/cli/{cmd}",
    },
    "cargo": {
        "family": "cargo", "list": _cargo_subcommands,
        "help_argv": lambda cmd: ["cargo", cmd, "--help"], "effect": _cargo_effect,
        "source_url": lambda cmd: f"https://doc.rust-lang.org/cargo/commands/cargo-{cmd}.html",
    },
    "poetry": {
        "family": "poetry", "list": _poetry_subcommands,
        "help_argv": lambda cmd: ["poetry", cmd, "--help"], "effect": _poetry_effect,
        "source_url": lambda cmd: f"https://python-poetry.org/docs/cli/#{cmd}",
    },
    "pnpm": {
        "family": "pnpm", "list": _pnpm_subcommands,
        "help_argv": lambda cmd: ["pnpm", cmd, "--help"], "effect": _pnpm_effect,
        "source_url": lambda cmd: f"https://pnpm.io/cli/{cmd}",
    },
    "supabase": {
        "family": "supabase", "list": _supabase_subcommands,
        "help_argv": lambda cmd: ["supabase", cmd, "--help"], "effect": _supabase_effect,
        "source_url": lambda cmd: f"https://supabase.com/docs/reference/cli/supabase-{cmd}",
    },
}


def _synopsis(text: str, program: str, cmd: str) -> str:
    for raw in text.splitlines():
        s = raw.strip().lstrip("▲").strip()
        if s.lower().startswith("usage:"):
            return s
        if s.startswith(f"{program} {cmd}"):
            return s
    return f"{program} {cmd}"


def _build(program: str, family: str, cmd: str, flags: list[dict], synopsis: str,
           source_url: str, effect, captured_at: datetime) -> dict:
    subject_id = f"{program}-{cmd}"
    command = f"{program} {cmd}"
    subject = StructuredSubject(
        subject_id=subject_id, subject_kind="tool", name=command, family=family,
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED, provenance_claim_ids=[],
        created_at=captured_at, updated_at=captured_at,
    )
    caps: list[StructuredCapability] = [
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "existence", "command"),
            subject_id=subject_id, capability_type="existence", title=f"{command} exists",
            detail=_validated_detail({
                "kind": "existence", "command": command, "source_url": source_url,
                "usage_signature": synopsis, "runtime_verified": True, "synopsis": synopsis,
            }),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=captured_at, updated_at=captured_at,
        ),
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "invocation", "usage"),
            subject_id=subject_id, capability_type="invocation", title=f"{command} invocation",
            detail=_validated_detail({
                "kind": "invocation", "command": command, "source_url": source_url,
                "usage_signature": synopsis, "synopsis": synopsis,
                "argv_schema": {"program": program, "subcommand_path": [cmd], "positionals": []},
                "flag_schema": flags,
            }),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.LOW,
            created_at=captured_at, updated_at=captured_at,
        ),
    ]
    for flag in flags:
        caps.append(StructuredCapability(
            capability_id=_stable_row_id(subject_id, "configuration", flag["name"]),
            subject_id=subject_id, capability_type="configuration",
            title=f"{command} flag {flag['name']}",
            detail=_validated_detail({
                "kind": "configuration", "command": command, "source_url": source_url,
                "usage_signature": synopsis, "flag": flag,
            }),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.98, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=captured_at, updated_at=captured_at,
        ))
    constraints = [StructuredConstraint(
        constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
        subject_id=subject_id, constraint_kind="environment",
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        detail={"command": command, "source_url": source_url, "requires_binary": program,
                "runtime_verified": True},
        created_at=captured_at, updated_at=captured_at,
    )]
    kind, destr, rev, mut, cost, expose, reason = effect(cmd)
    effects = [StructuredEffect(
        effect_id=_stable_row_id(subject_id, "effect", kind),
        subject_id=subject_id, effect_kind=kind,
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        destructive=destr, reversible=rev, mutates_remote_state=mut,
        may_cost_money=cost, may_expose_secrets=expose,
        detail={"command": command, "source_url": source_url, "classification_reason": reason},
        created_at=captured_at, updated_at=captured_at,
    )]
    return {"subject": subject, "capabilities": caps, "constraints": constraints, "effects": effects}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tool", required=True, choices=sorted(_TOOLS))
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = _TOOLS[args.tool]
    program = args.tool
    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0, "unparsed": 0}
    skipped: list[str] = []

    for cmd in cfg["list"]():
        try:
            text = _run(cfg["help_argv"](cmd))
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{cmd}({exc})")
            continue
        if not text.strip():
            skipped.append(f"{cmd}(empty)")
            continue
        flags, unparsed = flat._parse_flags(text)
        synopsis = _synopsis(text, program, cmd)
        bundle = _build(program, cfg["family"], cmd, flags, synopsis,
                        cfg["source_url"](cmd), cfg["effect"], now)
        totals["subjects"] += 1
        totals["capabilities"] += len(bundle["capabilities"])
        totals["constraints"] += len(bundle["constraints"])
        totals["effects"] += len(bundle["effects"])
        totals["unparsed"] += unparsed
        if store is not None:
            store.upsert_subject_graph(
                bundle["subject"], capabilities=bundle["capabilities"],
                constraints=bundle["constraints"], effects=bundle["effects"],
            )

    print(f"{program} ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    if skipped:
        print(f"  skipped {len(skipped)}: {skipped[:8]}{'...' if len(skipped) > 8 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
