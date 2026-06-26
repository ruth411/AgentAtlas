"""Structured ingest of the `openssl` command suite (synopsis + desc-list).

`openssl` is a multi-command tool. Subcommands are enumerated with
``openssl list-standard-commands``; each ``openssl <cmd> -help`` prints a usage
synopsis and (for most commands) a two-column option list with descriptions.
This walker:

- reuses the flat two-column parser for the rich description list (cut off
  before trailing choice grids such as "Valid ciphername values:"),
- falls back to parsing the bracketed usage synopsis for synopsis-only
  commands,
- records commands with neither as existence-only (the subcommand is real —
  it came from ``list-standard-commands`` — but exposes no parseable options).

Guarantees match the other ingesters: every flag traces to a line we parsed,
unparseable option lines are counted, capabilities are L3 (the binary produced
them), effect safety is L2 (classified).

Usage:
    structured_ingest_openssl.py --database sqlite:///backend/ayiru_v0.2_bulk.db [--dry-run]
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
from app.services.structured_cli_ingestion import (  # noqa: E402
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
import structured_ingest_flatcli as flat  # noqa: E402

_DOCS = "https://www.openssl.org/docs/man1.1.1/man1/openssl-{cmd}.html"
_FORBIDDEN = ("|", ";", "&", ">", "<", "$(", "`", "\n", "\r")

# Commands that talk to the network.
_NETWORK = {"s_client", "s_server", "s_time", "ocsp", "ts"}
# Commands that generate or handle private-key / password material.
_SECRET = {
    "genrsa", "gendsa", "gendh", "genpkey", "dsaparam", "ecparam", "dhparam",
    "dh", "dsa", "ec", "rsa", "pkey", "pkcs8", "pkcs12", "enc", "passwd",
    "rand", "rsautl", "pkeyutl", "spkac", "sess_id",
}


def _run(argv: list[str]) -> str:
    for tok in argv:
        if any(f in tok for f in _FORBIDDEN):
            raise RuntimeError(f"unsafe token: {tok!r}")
    p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    return (p.stdout or "") + (p.stderr or "")


def _subcommands() -> list[str]:
    out = _run(["openssl", "list-standard-commands"])
    cmds = [c for c in out.split() if re.fullmatch(r"[a-z][a-z0-9_]*", c)]
    return sorted(set(cmds))


def _usage_block(text: str) -> list[str]:
    """The usage synopsis lines (from `usage:` until a blank line)."""
    lines = text.splitlines()
    block: list[str] = []
    in_usage = False
    for line in lines:
        if re.match(r"(?i)^usage:", line):
            in_usage = True
            block.append(line)
            continue
        if in_usage:
            if not line.strip():
                break
            if re.match(r"^\s", line):  # continuation of the synopsis
                block.append(line)
            else:
                break
    return block


def _desc_block(text: str) -> str:
    """The contiguous indented option-description list, excluding any trailing
    left-aligned section (e.g. "Valid ciphername values:" and its grid)."""
    lines = text.splitlines()
    # Start after the usage synopsis ends.
    start = 0
    seen_usage = False
    for i, line in enumerate(lines):
        if re.match(r"(?i)^usage:", line):
            seen_usage = True
        elif seen_usage and not line.strip():
            start = i + 1
            break
    out: list[str] = []
    started = False
    for line in lines[start:]:
        if not line.strip():
            if started:
                out.append(line)
            continue
        if not re.match(r"^\s", line):
            # A left-aligned line: a new section header — stop before its body.
            break
        out.append(line)
        started = True
    return "\n".join(out)


def _synopsis_flags(usage_lines: list[str], known: set[str]) -> list[dict]:
    """Parse bracketed option groups from a usage synopsis into flag dicts.

    Each group is one option: ``[-text]`` (bool), ``[-in file]`` (value),
    ``[-certform der | pem]`` (choices). Options already captured from a
    richer description list (``known``) are skipped.
    """
    text = " ".join(line.strip() for line in usage_lines)
    flags: list[dict] = []
    seen: set[str] = set()
    for group in re.findall(r"\[([^\[\]]+)\]", text):
        g = group.strip()
        m = re.match(r"^(-[A-Za-z0-9][\w.-]*)(?:\s+(.*))?$", g)
        if not m:
            continue
        name = m.group(1)
        if name in known or name in seen:
            continue
        seen.add(name)
        rest = (m.group(2) or "").strip()
        choices: list[str] = []
        value_name = None
        if rest:
            if "|" in rest:
                choices = [c.strip() for c in rest.split("|") if c.strip() and not c.strip().startswith("-")]
                value_name = None
            else:
                value_name = rest.split()[0]
        flags.append({
            "name": name, "short": None, "takes_value": value_name is not None,
            "value_name": value_name, "value_type": flat._value_meta(value_name)[1] if value_name else "boolean",
            "repeatable": False, "required": False, "deprecated": False, "inherited": False,
            "description": f"{name} option of `openssl {usage_lines[0].split()[1] if len(usage_lines[0].split())>1 else ''}`.".strip(),
            "default": None, "choices": choices,
        })
    return flags


def _effect(cmd: str) -> tuple[str, bool, bool, bool, bool, bool, str]:
    c = f"openssl {cmd}"
    if cmd in _NETWORK:
        return ("network", False, True, False, False, cmd in _SECRET,
                f"`{c}` opens a TLS/network connection.")
    if cmd in _SECRET:
        return ("secret_exposure", False, True, False, False, True,
                f"`{c}` generates or handles private-key, password or other secret material.")
    return ("filesystem", False, True, False, False, False,
            f"`{c}` parses or converts local cryptographic files without network or remote effects.")


def _build(cmd: str, flags: list[dict], synopsis: str, captured_at: datetime) -> dict:
    subject_id = f"openssl-{cmd}"
    command = f"openssl {cmd}"
    source_url = _DOCS.format(cmd=cmd)
    subject = StructuredSubject(
        subject_id=subject_id, subject_kind="tool", name=command, family="openssl",
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
                "argv_schema": {"program": "openssl", "subcommand_path": [cmd], "positionals": []},
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
        detail={"command": command, "source_url": source_url, "requires_binary": "openssl",
                "runtime_verified": True},
        created_at=captured_at, updated_at=captured_at,
    )]
    kind, destr, rev, mut, cost, expose, reason = _effect(cmd)
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
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0, "unparsed": 0}
    rich = thin = existence_only = 0

    for cmd in _subcommands():
        text = _run(["openssl", cmd, "-help"])
        usage = _usage_block(text)
        synopsis = usage[0].strip() if usage else f"openssl {cmd}"
        desc_flags, unparsed = flat._parse_flags(_desc_block(text))
        known = {f["name"] for f in desc_flags}
        syn_flags = _synopsis_flags(usage, known) if usage else []
        flags = desc_flags + syn_flags
        if desc_flags:
            rich += 1
        elif flags:
            thin += 1
        else:
            existence_only += 1
        bundle = _build(cmd, flags, synopsis, now)
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

    print(f"openssl ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  rich(desc-list)={rich} synopsis-only={thin} existence-only={existence_only}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
