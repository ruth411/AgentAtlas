"""Structured ingest of the `pip` CLI (argparse/optparse family).

Runs each `pip <command> --help` from the *installed* pip, parses pip's
section-based help (``General Options:`` / ``<Name> Options:`` for flags,
``Commands:`` for subcommands), captures every option spelling and its
``<metavar>``, classifies side effects, and persists runtime-verified
structured rows. Mirrors the ansible-cli ingester's guarantees:

- Every flag/subcommand traces to a line we actually parsed from ``--help``.
- Lines we cannot parse are counted in ``unparsed`` and never invented.
- Each row is ``L3_runtime_verified`` because the installed binary produced it.

Usage:
    structured_ingest_pip.py --database sqlite:///backend/ayiru_v0.2_bulk.db [--dry-run]
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

_DOCS = "https://pip.pypa.io/en/stable/cli/{slug}/"
_FORBIDDEN = ("|", ";", "&", ">", "<", "$(", "`", "\n", "\r")

# A flag line, e.g. "  -r, --requirement <file>   Install from ...".
_FLAG_LINE = re.compile(r"^\s{2,}(?:-[A-Za-z],\s+)?(?:--[A-Za-z][A-Za-z0-9-]*|-[A-Za-z])")
# An option-section header, e.g. "General Options:", "Install Options:".
_OPT_SECTION = re.compile(r"^[A-Z][A-Za-z ]*Options:\s*$")
# A subcommand line under "Commands:", e.g. "  install   Install packages.".
_SUBCMD = re.compile(r"^\s{2,}([a-z][a-z0-9-]+)\s{2,}\S")
# One token of an option spec: "--long" / "-s", with an optional "<metavar>".
_OPT_TOKEN = re.compile(r"^(--[A-Za-z][A-Za-z0-9-]*|-[A-Za-z])(?:\s+(.+))?$")
# Subcommands that are help/meta and have no useful own surface to recurse into
# more than once. `help` is included so we ingest it but don't loop.
_NO_RECURSE = frozenset({"help", "completion"})


class PipCliError(RuntimeError):
    pass


def _run_help(argv: tuple[str, ...]) -> str:
    cmd = ["pip", *argv[1:], "--help"]
    for tok in cmd:
        if any(f in tok for f in _FORBIDDEN):
            raise PipCliError(f"unsafe token: {tok!r}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        raise PipCliError(f"empty help for {' '.join(argv)}")
    return out


def _value_meta(metavar: str | None) -> tuple[str | None, str, list[str]]:
    """(value_name, value_type, choices) from a pip metavar like ``<file>``."""
    if not metavar:
        return None, "boolean", []
    name = metavar.strip().strip("<>").split()[0].lower()
    return name, _infer_value_type(name), []


def _parse_optspec(spec: str) -> list[tuple[str, str | None]]:
    """Parse ``-r, --requirement <file>`` into [(name, metavar_or_None), ...]."""
    out: list[tuple[str, str | None]] = []
    for part in re.split(r",\s+", spec):
        m = _OPT_TOKEN.match(part.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _split_optspec(stripped: str) -> tuple[str, str]:
    """Split an option entry into (option-spec, same-line description)."""
    m = re.search(r"\s{2,}", stripped)
    if m:
        return stripped[: m.start()].strip(), stripped[m.end():].strip()
    return stripped.strip(), ""


def _parse_help(argv: tuple[str, ...], text: str) -> dict:
    flags: list[dict] = []
    subcommands: list[str] = []
    synopsis = ""
    unparsed = 0
    section = "preamble"
    seen_flags: set[str] = set()
    current: list[dict] | None = None  # flag group whose wrapped desc we're in

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            current = None
            continue
        stripped = line.strip()
        if stripped.lower().startswith("usage:") and not synopsis:
            synopsis = stripped
            continue
        if stripped == "Commands:":
            section, current = "commands", None
            continue
        if _OPT_SECTION.match(stripped):
            section, current = "options", None
            continue
        if stripped.endswith(":") and not line.startswith(" "):
            # Description:, etc. — leave option/command mode.
            section, current = "preamble", None
            continue

        if section == "options":
            if _FLAG_LINE.match(line):
                spec, desc = _split_optspec(stripped)
                tokens = _parse_optspec(spec)
                if not tokens:
                    unparsed += 1
                    continue
                longs = [(n, mv) for n, mv in tokens if n.startswith("--")]
                shorts = [(n, mv) for n, mv in tokens if not n.startswith("--")]
                metavar = next((mv for _, mv in tokens if mv), None)
                short = shorts[0][0] if shorts else None
                names = [n for n, _ in longs] if longs else [s for s, _ in shorts]
                group: list[dict] = []
                for name in names:
                    if name in seen_flags:
                        continue
                    seen_flags.add(name)
                    value_name, value_type, choices = _value_meta(metavar)
                    flag = {
                        "name": name,
                        "short": short if name.startswith("--") else None,
                        "takes_value": metavar is not None,
                        "value_name": value_name,
                        "value_type": value_type,
                        "repeatable": False,
                        "required": False,
                        "deprecated": False,
                        "inherited": False,
                        "description": desc,
                        "default": None,
                        "choices": choices,
                    }
                    flags.append(flag)
                    group.append(flag)
                current = group or None
                continue
            if current is not None and line.startswith("  "):
                for flag in current:
                    flag["description"] = (flag["description"] + " " + stripped).strip()
                    if "multiple times" in flag["description"]:
                        flag["repeatable"] = True
                    if "deprecated" in flag["description"].lower():
                        flag["deprecated"] = True
                continue
            unparsed += 1
        elif section == "commands":
            m = _SUBCMD.match(line)
            if m:
                subcommands.append(m.group(1))

    for flag in flags:
        if not flag["description"]:
            flag["description"] = f"{flag['name']} option of `{' '.join(argv)}`."
    return {
        "synopsis": synopsis or " ".join(argv),
        "flags": flags,
        "subcommands": subcommands,
        "positionals": [],
        "unparsed": unparsed,
    }


def _effect_profile(argv: tuple[str, ...]) -> tuple[str, bool, bool, bool, bool, bool, str]:
    """(effect_kind, destructive, reversible, mutates_remote, may_cost, may_expose, reason)."""
    leaf = argv[-1]
    cmd = " ".join(argv)
    if leaf == "uninstall" or (len(argv) >= 3 and argv[1] == "cache" and leaf in {"remove", "purge"}):
        return ("destructive", True, True, False, False, False,
                f"`{cmd}` removes installed packages or cached files from the local environment.")
    if leaf in {"install", "wheel", "download"}:
        return ("mutation", False, True, False, False, False,
                f"`{cmd}` fetches packages over the network and writes them into the local environment.")
    if argv[1:2] == ("config",) and leaf in {"set", "unset", "edit"}:
        return ("mutation", False, True, False, False, False,
                f"`{cmd}` writes pip configuration.")
    if leaf in {"search", "index", "lock"} or leaf == "install":
        return ("network", False, True, False, False, False,
                f"`{cmd}` queries a package index over the network.")
    return ("network", False, True, False, False, False,
            f"`{cmd}` reads or inspects the local environment without changing installed packages.")


def _build_bundle(argv: tuple[str, ...], parsed: dict, captured_at: datetime) -> dict:
    subject_id = "-".join(argv)
    command = " ".join(argv)
    source_url = _DOCS.format(slug="_".join(argv))
    subject = StructuredSubject(
        subject_id=subject_id,
        subject_kind="tool",
        name=command,
        family="pip",
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        provenance_claim_ids=[],
        created_at=captured_at,
        updated_at=captured_at,
    )
    flag_schema = list(parsed["flags"])
    caps: list[StructuredCapability] = [
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "existence", "command"),
            subject_id=subject_id,
            capability_type="existence",
            title=f"{command} exists",
            detail=_validated_detail({
                "kind": "existence", "command": command, "source_url": source_url,
                "usage_signature": parsed["synopsis"], "runtime_verified": True,
                "synopsis": parsed["synopsis"],
            }),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=captured_at, updated_at=captured_at,
        ),
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "invocation", "usage"),
            subject_id=subject_id,
            capability_type="invocation",
            title=f"{command} invocation",
            detail=_validated_detail({
                "kind": "invocation", "command": command, "source_url": source_url,
                "usage_signature": parsed["synopsis"], "synopsis": parsed["synopsis"],
                "argv_schema": {"program": "pip", "subcommand_path": list(argv[1:]),
                                "positionals": parsed["positionals"]},
                "flag_schema": flag_schema,
            }),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.LOW,
            created_at=captured_at, updated_at=captured_at,
        ),
    ]
    for flag in flag_schema:
        caps.append(StructuredCapability(
            capability_id=_stable_row_id(subject_id, "configuration", flag["name"]),
            subject_id=subject_id, capability_type="configuration",
            title=f"{command} flag {flag['name']}",
            detail=_validated_detail({
                "kind": "configuration", "command": command, "source_url": source_url,
                "usage_signature": parsed["synopsis"], "flag": flag,
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
        detail={"command": command, "source_url": source_url, "requires_binary": "pip",
                "runtime_verified": True},
        created_at=captured_at, updated_at=captured_at,
    )]
    kind, destr, rev, mut, cost, expose, reason = _effect_profile(argv)
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
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    queue: list[tuple[str, ...]] = [("pip",)]
    handled: set[tuple[str, ...]] = set()
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0, "unparsed": 0}

    while queue:
        argv = queue.pop(0)
        if argv in handled:
            continue
        handled.add(argv)
        try:
            text = _run_help(argv)
        except PipCliError as exc:
            print(f"  SKIP {' '.join(argv)}: {exc}")
            continue
        parsed = _parse_help(argv, text)
        if argv == ("pip",) or argv[-1] not in _NO_RECURSE:
            for sub in parsed["subcommands"]:
                queue.append((*argv, sub))
        bundle = _build_bundle(argv, parsed, now)
        totals["subjects"] += 1
        totals["capabilities"] += len(bundle["capabilities"])
        totals["constraints"] += len(bundle["constraints"])
        totals["effects"] += len(bundle["effects"])
        totals["unparsed"] += parsed["unparsed"]
        if store is not None:
            store.upsert_subject_graph(
                bundle["subject"], capabilities=bundle["capabilities"],
                constraints=bundle["constraints"], effects=bundle["effects"],
            )

    print(f"pip CLI ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  subjects handled: {len(handled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
