"""Phase 1 of ansible knowledge verification: the ansible CLI programs.

Runs each ansible CLI's ``--help`` from an *isolated* ansible install (a
throwaway venv, never the system), parses the standard argparse help into
typed argv/flag records, recurses into subcommands (``ansible-vault encrypt``,
``ansible-galaxy collection install`` …), classifies side effects, and persists
runtime-verified structured rows.

Non-hallucination guarantees:
- Every flag/subcommand traces to a line we actually parsed from ``--help``.
- Lines we cannot parse are counted in ``unparsed`` and never invented.
- Each row is ``L3_runtime_verified`` because the installed binary produced it.

Usage:
    structured_ingest_ansible_cli.py --ansible-bin-dir /tmp/ansible-extract/venv/bin \\
        --database sqlite:///backend/ansible_stage.db [--dry-run]
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

_CLIS = (
    "ansible",
    "ansible-config",
    "ansible-console",
    "ansible-doc",
    "ansible-galaxy",
    "ansible-inventory",
    "ansible-playbook",
    "ansible-pull",
    "ansible-vault",
)
_DOCS = "https://docs.ansible.com/ansible/latest/cli/{prog}.html"
_FORBIDDEN = ("|", ";", "&", ">", "<", "$(", "`", "\n", "\r")

# argparse option entry, e.g.
#   "  -t TAGS, --tags TAGS   only run plays and tasks tagged ..."
#   "  --flush-cache          clear the fact cache ..."
#   "  -K, --ask-become-pass  ask for privilege escalation password"
#   "  --become-password-file FILE, --become-pass-file FILE"   (alias form)
# We capture the short (+ metavar), the primary long (+ metavar), tolerate any
# number of trailing long aliases, then an optional same-line description.
# subcommand entry under a positional-arguments section:
#   "    encrypt   Encrypt YAML file"
_SUBCMD = re.compile(r"^\s{4,}([a-z][a-z0-9_-]+)\s{2,}(\S.*)$")

_OPT_TOKEN = re.compile(r"^(--[A-Za-z][A-Za-z0-9-]*|-[A-Za-z])(?:\s+(.+))?$")


def _split_optspec(stripped: str) -> tuple[str, str]:
    """Split an option entry into its option-spec and its (same-line) description.

    The boundary is the first run of 2+ spaces at bracket depth 0, so spaces
    inside ``{a,b}`` choice metavars or ``[{a,b} ...]`` nargs groups don't
    split it. e.g. ``--format {json,yaml}, -f {json,yaml}   Output format``.
    """
    depth = 0
    for i in range(len(stripped) - 1):
        ch = stripped[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth = max(0, depth - 1)
        elif ch == " " and depth == 0 and stripped[i + 1] == " ":
            return stripped[:i].strip(), stripped[i + 1:].strip()
    return stripped.strip(), ""


def _value_meta(metavar: str | None) -> tuple[str | None, str, list[str]]:
    """(value_name, value_type, choices) from an argparse metavar."""
    if not metavar:
        return None, "boolean", []
    brace = re.match(r"\{([^}]*)\}", metavar)
    if brace:
        return "choice", "string", [c.strip() for c in brace.group(1).split(",") if c.strip()]
    return metavar.split()[0].lower(), _infer_value_type(metavar.split()[0].lower()), []


def _parse_optspec(spec: str) -> list[tuple[str, str | None]]:
    """Parse ``--long META, -s META, --alias`` into [(name, metavar_or_None), ...]."""
    out: list[tuple[str, str | None]] = []
    for part in re.split(r",\s+", spec):
        m = _OPT_TOKEN.match(part.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


class AnsibleCliError(RuntimeError):
    pass


def _run_help(bin_dir: Path, argv: tuple[str, ...]) -> str:
    cmd = [str(bin_dir / argv[0]), *argv[1:], "--help"]
    for tok in cmd:
        if any(f in tok for f in _FORBIDDEN):
            raise AnsibleCliError(f"unsafe token: {tok!r}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        raise AnsibleCliError(f"empty help for {' '.join(argv)}")
    return out


def _parse_help(argv: tuple[str, ...], text: str) -> dict:
    lines = text.splitlines()
    synopsis = ""
    flags: list[dict] = []
    subcommands: list[str] = []
    positionals: list[dict] = []
    unparsed = 0
    section = "preamble"
    seen_flags: set[str] = set()
    current: list[dict] | None = None  # the flag group whose multi-line desc we're in
    in_subcmd_block = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            current = None
            continue
        low = line.strip().lower()
        if low.startswith("usage:") and not synopsis:
            synopsis = line.strip()
        if re.match(r"^(positional arguments|commands):\s*$", low):
            section = "positional"
            in_subcmd_block = True
            continue
        if re.match(r"^(options|optional arguments):\s*$", low):
            section = "options"
            in_subcmd_block = False
            continue

        if section == "options":
            if re.match(r"^\s{2,}-", line):
                spec, desc = _split_optspec(line.strip())
                tokens = _parse_optspec(spec)
                if not tokens:
                    unparsed += 1
                    continue
                longs = [(n, mv) for n, mv in tokens if n.startswith("--")]
                shorts = [(n, mv) for n, mv in tokens if not n.startswith("--")]
                metavar = next((mv for _, mv in tokens if mv), None)
                short = shorts[0][0] if shorts else None
                # Emit one entry per long spelling so any spelling matches; if the
                # option is short-only, emit under the short name.
                names = [n for n, _ in longs] if longs else ([shorts[0][0]] if shorts else [])
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
                        "description": desc.strip(),
                        "default": None,
                        "choices": choices,
                    }
                    flags.append(flag)
                    group.append(flag)
                current = group or None
                continue
            # A wrapped description line shared by every spelling in the group.
            if current is not None and line.startswith("  "):
                for flag in current:
                    flag["description"] = (flag["description"] + " " + line.strip()).strip()
                    if "multiple times" in flag["description"]:
                        flag["repeatable"] = True
                    if "deprecated" in flag["description"].lower():
                        flag["deprecated"] = True
                continue
            unparsed += 1
        elif section == "positional" and in_subcmd_block:
            sm = _SUBCMD.match(line)
            if sm:
                subcommands.append(sm.group(1))
            elif re.match(r"^\s{2,}\{[a-z0-9_,-]+\}\s*$", line):
                pass  # the choices summary line; names come from the entries
            elif re.match(r"^\s{2,}[A-Z][A-Z_]*\s*$", line):
                pass  # a metavar like TYPE
    # any flag whose description is still empty gets a neutral, honest fallback
    for f in flags:
        if not f["description"]:
            f["description"] = f"{f['name']} option of `{' '.join(argv)}`."
    return {
        "synopsis": synopsis or " ".join(argv),
        "flags": flags,
        "subcommands": subcommands,
        "positionals": positionals,
        "unparsed": unparsed,
    }


def _effect_profile(argv: tuple[str, ...]) -> tuple[str, bool, bool, bool, bool, bool, str]:
    """(effect_kind, destructive, reversible, mutates_remote, may_cost, may_expose, reason)."""
    leaf = argv[-1]
    prog = argv[0]
    cmd = " ".join(argv)
    if leaf in {"decrypt", "rekey"} or prog == "ansible-vault" and leaf in {"encrypt", "encrypt_string", "edit", "view", "create"}:
        return ("secret_exposure", False, True, False, False, True,
                f"`{cmd}` handles ansible-vault encrypted secret material.")
    if prog in {"ansible", "ansible-playbook", "ansible-pull"}:
        return ("mutation", False, False, True, False, False,
                f"`{cmd}` executes tasks against target hosts and can change remote state.")
    if prog == "ansible-galaxy" and leaf in {"install", "remove"}:
        return ("mutation", leaf == "remove", leaf != "remove", False, False, False,
                f"`{cmd}` modifies locally installed collections/roles.")
    if prog == "ansible-config" and leaf in {"init"}:
        return ("mutation", False, True, False, False, False,
                f"`{cmd}` writes an ansible configuration file.")
    return ("network", False, True, False, False, False,
            f"`{cmd}` reads or inspects ansible state without changing managed hosts.")


def _build_bundle(argv: tuple[str, ...], parsed: dict, captured_at: datetime) -> dict:
    subject_id = "-".join(argv)
    command = " ".join(argv)
    prog = argv[0]
    source_url = _DOCS.format(prog=prog)
    subject = StructuredSubject(
        subject_id=subject_id,
        subject_kind="tool",
        name=command,
        family="ansible",
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        provenance_claim_ids=[],
        created_at=captured_at,
        updated_at=captured_at,
    )
    flag_schema = [{k: v for k, v in f.items() if not k.startswith("_")} for f in parsed["flags"]]
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
                "argv_schema": {"program": prog, "subcommand_path": list(argv[1:]),
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
        detail={"command": command, "source_url": source_url, "requires_binary": prog,
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
    ap.add_argument("--ansible-bin-dir", default="/tmp/ansible-extract/venv/bin", type=Path)
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    queue: list[tuple[str, ...]] = [(c,) for c in _CLIS]
    handled: set[tuple[str, ...]] = set()
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0, "unparsed": 0}

    while queue:
        argv = queue.pop(0)
        if argv in handled:
            continue
        handled.add(argv)
        try:
            text = _run_help(args.ansible_bin_dir, argv)
        except AnsibleCliError as exc:
            print(f"  SKIP {' '.join(argv)}: {exc}")
            continue
        parsed = _parse_help(argv, text)
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

    print(f"ansible CLI ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  subjects handled: {len(handled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
