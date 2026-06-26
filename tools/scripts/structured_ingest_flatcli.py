"""Structured ingest of flat two-column `--help` CLIs (curl, jq, ...).

Some tools print a single flat option list (``  -d, --data <data>   desc``)
rather than cobra/argparse sections or a bare usage synopsis. This walker runs
the tool's real help, parses that two-column list, and persists runtime-verified
structured rows. Same guarantees as the ansible/pip ingesters:

- Every flag traces to a line we actually parsed from the tool's own help.
- Lines we cannot parse are counted in ``unparsed`` and never invented.
- Capabilities are ``L3_runtime_verified`` (the installed binary produced them);
  effect safety booleans are ``L2_source_verified`` (classified, not asserted).

Each tool is a single flat subject (no subcommands). Add a tool by extending
``_REGISTRY``.

Usage:
    structured_ingest_flatcli.py --tool curl --database sqlite:///backend/ayiru_v0.2_bulk.db [--dry-run]
    structured_ingest_flatcli.py --tool all  --database sqlite:///backend/ayiru_v0.2_bulk.db
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.enums import (  # noqa: E402
    ConfidenceBand,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.services.structured_cli_ingestion import (  # noqa: E402
    _infer_value_type,
    _stable_row_id,
    _validated_detail,
)
from app.services.structured_knowledge_store import (  # noqa: E402
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)

_FORBIDDEN = ("|", ";", "&", ">", "<", "$(", "`", "\n", "\r")
# Two-column option line: indent, an option spec, 2+ spaces, a description.
_TWO_COL = re.compile(r"^\s+(\S.*?)\s{2,}(\S.*)$")
# Fallback for tools whose description column is only one space away: an option
# cluster (one or more dash-options, comma-separated) plus up to two metavars,
# then a single space and the description. e.g. "--slurpfile name file set ...".
_ONE_SPACE = re.compile(
    r"^\s+(-{1,2}[A-Za-z0-9][\w.-]*(?:,\s*-{1,2}[A-Za-z0-9][\w.-]*)*"
    r"(?:\s+(?:<[^>]+>|[A-Za-z][\w-]*)){0,2})\s+(\S.*)$"
)
# A bare option line with no description (just the flag, optionally with up to
# two metavars), e.g. "  -verify_return_error" or "  -in file".
_BARE_OPT = re.compile(
    r"^\s+(-{1,2}[A-Za-z0-9][\w.-]*(?:,\s*-{1,2}[A-Za-z0-9][\w.-]*)*"
    r"(?:\s+(?:<[^>]+>|[A-Za-z][\w-]*)){0,2})\s*$"
)
# One token of an option spec: "--long" / "-s" (dots allowed: --http1.1), with
# an optional metavar tail.
_OPT_TOKEN = re.compile(r"^(--[A-Za-z0-9][A-Za-z0-9._-]*|-[A-Za-z0-9][A-Za-z0-9._-]*)(?:\s+(.+))?$")


@dataclass(frozen=True)
class FlatTool:
    program: str
    help_argv: tuple[str, ...]
    source_url: str
    # (effect_kind, destructive, reversible, mutates_remote, may_cost, may_expose, reason)
    effect: tuple[str, bool, bool, bool, bool, bool, str]


class FlatCliError(RuntimeError):
    pass


def _run_help(argv: tuple[str, ...]) -> str:
    for tok in argv:
        if any(f in tok for f in _FORBIDDEN):
            raise FlatCliError(f"unsafe token: {tok!r}")
    proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=30)
    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        raise FlatCliError(f"empty help for {' '.join(argv)}")
    return out


def _value_meta(metavar: str | None) -> tuple[str | None, str, list[str]]:
    if not metavar:
        return None, "boolean", []
    name = metavar.strip().strip("<>").strip()
    base = name.split()[0].lower() if name else ""
    return name or None, _infer_value_type(base), []


def _parse_optspec(spec: str) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for part in re.split(r",\s+", spec):
        part = part.strip()
        # The "-[no]name" toggle convention (sqlite3, etc.) is a real pair of
        # boolean options: expand it to "-name" and "-noname".
        toggle = re.match(r"^-\[no\]([A-Za-z][\w-]*)$", part)
        if toggle:
            out.append((f"-{toggle.group(1)}", None))
            out.append((f"-no{toggle.group(1)}", None))
            continue
        m = _OPT_TOKEN.match(part)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _parse_flags(text: str) -> tuple[list[dict], int]:
    flags: list[dict] = []
    unparsed = 0
    seen: set[str] = set()
    current: list[dict] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            current = None
            continue
        # A flag line starts (after indent) with a dash.
        if re.match(r"^\s+-", line):
            m = _TWO_COL.match(line) or _ONE_SPACE.match(line)
            if m:
                spec, desc = m.group(1).strip(), m.group(2).strip()
            else:
                mb = _BARE_OPT.match(line)
                if not mb:
                    # An option-looking line we can't parse (e.g. "--" marker).
                    current = None
                    unparsed += 1
                    continue
                spec, desc = mb.group(1).strip(), ""
            tokens = _parse_optspec(spec)
            if not tokens:
                current = None
                unparsed += 1
                continue
            longs = [(n, mv) for n, mv in tokens if n.startswith("--")]
            shorts = [(n, mv) for n, mv in tokens if not n.startswith("--")]
            metavar = next((mv for _, mv in tokens if mv), None)
            short = shorts[0][0] if shorts else None
            names = [n for n, _ in longs] if longs else [s for s, _ in shorts]
            group: list[dict] = []
            for name in names:
                if name in seen:
                    continue
                seen.add(name)
                value_name, value_type, choices = _value_meta(metavar)
                flag = {
                    "name": name,
                    "short": short if name.startswith("--") else None,
                    "takes_value": metavar is not None,
                    "value_name": value_name,
                    "value_type": value_type,
                    "repeatable": False,
                    "required": False,
                    "deprecated": "deprecated" in desc.lower(),
                    "inherited": False,
                    "description": desc or f"{name} (flag; no description in --help)",
                    "default": None,
                    "choices": choices,
                }
                flags.append(flag)
                group.append(flag)
            current = group or None
            continue
        # Wrapped description continuation of the current flag group.
        if current is not None and re.match(r"^\s{4,}\S", line):
            for flag in current:
                flag["description"] = (flag["description"] + " " + line.strip()).strip()
            continue
        current = None
    return flags, unparsed


def _build_bundle(tool: FlatTool, flags: list[dict], synopsis: str, captured_at: datetime) -> dict:
    subject_id = tool.program
    command = tool.program
    subject = StructuredSubject(
        subject_id=subject_id, subject_kind="tool", name=command, family=tool.program,
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED, provenance_claim_ids=[],
        created_at=captured_at, updated_at=captured_at,
    )
    caps: list[StructuredCapability] = [
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "existence", "command"),
            subject_id=subject_id, capability_type="existence", title=f"{command} exists",
            detail=_validated_detail({
                "kind": "existence", "command": command, "source_url": tool.source_url,
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
                "kind": "invocation", "command": command, "source_url": tool.source_url,
                "usage_signature": synopsis, "synopsis": synopsis,
                "argv_schema": {"program": tool.program, "subcommand_path": [], "positionals": []},
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
                "kind": "configuration", "command": command, "source_url": tool.source_url,
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
        detail={"command": command, "source_url": tool.source_url,
                "requires_binary": tool.program, "runtime_verified": True},
        created_at=captured_at, updated_at=captured_at,
    )]
    kind, destr, rev, mut, cost, expose, reason = tool.effect
    effects = [StructuredEffect(
        effect_id=_stable_row_id(subject_id, "effect", kind),
        subject_id=subject_id, effect_kind=kind,
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        destructive=destr, reversible=rev, mutates_remote_state=mut,
        may_cost_money=cost, may_expose_secrets=expose,
        detail={"command": command, "source_url": tool.source_url, "classification_reason": reason},
        created_at=captured_at, updated_at=captured_at,
    )]
    return {"subject": subject, "capabilities": caps, "constraints": constraints, "effects": effects}


def _synopsis(text: str, program: str) -> str:
    for raw in text.splitlines():
        s = raw.strip()
        if s.lower().startswith("usage:"):
            return s
    return program


_REGISTRY: dict[str, FlatTool] = {
    "curl": FlatTool(
        program="curl",
        help_argv=("curl", "--help", "all"),
        source_url="https://curl.se/docs/manpage.html",
        effect=("network", False, True, False, False, True,
                "`curl` transfers data over the network; it can send credentials "
                "(-u), request bodies (--data) and upload files (-T), so it may "
                "expose secrets and mutate remote state depending on the request."),
    ),
    "jq": FlatTool(
        program="jq",
        help_argv=("jq", "--help"),
        source_url="https://jqlang.github.io/jq/manual/",
        effect=("compute", False, True, False, False, False,
                "`jq` transforms JSON from stdin/files to stdout; it performs no "
                "network or destructive filesystem side effects."),
    ),
    "sqlite3": FlatTool(
        program="sqlite3",
        help_argv=("sqlite3", "--help"),
        source_url="https://sqlite.org/cli.html",
        effect=("filesystem", False, True, False, False, False,
                "`sqlite3` opens, creates and queries a local SQLite database "
                "file; it reads and writes the local filesystem and performs no "
                "network or remote side effects (data changes are driven by the "
                "SQL the caller supplies)."),
    ),
}


def _ingest(tool: FlatTool, store, now: datetime) -> dict:
    text = _run_help(tool.help_argv)
    flags, unparsed = _parse_flags(text)
    bundle = _build_bundle(tool, flags, _synopsis(text, tool.program), now)
    if store is not None:
        store.upsert_subject_graph(
            bundle["subject"], capabilities=bundle["capabilities"],
            constraints=bundle["constraints"], effects=bundle["effects"],
        )
    return {"subjects": 1, "capabilities": len(bundle["capabilities"]),
            "constraints": len(bundle["constraints"]), "effects": len(bundle["effects"]),
            "unparsed": unparsed, "flags": len(flags)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tool", required=True, help="a registry key or 'all'")
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tools = list(_REGISTRY.values()) if args.tool == "all" else [_REGISTRY[args.tool]]
    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    for tool in tools:
        try:
            r = _ingest(tool, store, now)
        except (FlatCliError, KeyError) as exc:
            print(f"  SKIP {tool.program}: {exc}")
            continue
        print(f"{tool.program} flat ingest ({'dry-run' if args.dry_run else 'applied'}): "
              f"{r['flags']} flags, {r['capabilities']} caps, unparsed={r['unparsed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
