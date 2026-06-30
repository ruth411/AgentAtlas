"""Structured ingest of synopsis-only CLIs (awk, sed, ssh, rsync).

These tools on macOS often expose only a compact `usage:` surface rather than
full two-column `--help` output. This ingester parses the real runtime usage
text into machine-readable flags and persists one structured subject per tool.
Flags captured from synopsis text are still `L3_runtime_verified`; they simply
lack rich per-flag descriptions.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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

_FORBIDDEN = ("|", ";", "&", ">", "<", "$(", "`", "\n", "\r")


@dataclass(frozen=True)
class UsageTool:
    program: str
    help_argv: tuple[str, ...]
    source_url: str
    effect: tuple[str, bool, bool, bool, bool, bool, str]


class UsageCliError(RuntimeError):
    pass


def _run_help(argv: tuple[str, ...]) -> str:
    for tok in argv:
        if any(f in tok for f in _FORBIDDEN):
            raise UsageCliError(f"unsafe token: {tok!r}")
    proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=30)
    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        raise UsageCliError(f"empty help for {' '.join(argv)}")
    return out


def _clean_value_name(raw: str) -> str:
    return raw.strip().strip("[]<>").strip().rstrip(",")


def _value_meta(raw: str | None) -> tuple[str | None, str]:
    if not raw:
        return None, "boolean"
    name = _clean_value_name(raw)
    base = name.split()[0].lower() if name else ""
    return name or None, _infer_value_type(base)


def _append_flag(flags: list[dict], seen: set[str], name: str, value_raw: str | None) -> None:
    if name in seen:
        return
    seen.add(name)
    value_name, value_type = _value_meta(value_raw)
    flags.append(
        {
            "name": name,
            "short": None,
            "takes_value": value_name is not None,
            "value_name": value_name,
            "value_type": value_type,
            "repeatable": False,
            "required": False,
            "deprecated": False,
            "inherited": False,
            "description": f"{name} (flag parsed from usage synopsis)",
            "default": None,
            "choices": [],
        }
    )


def _parse_segment(segment: str, flags: list[dict], seen: set[str]) -> None:
    seg = segment.strip()
    if not seg or not seg.startswith("-"):
        return
    if "|" in seg:
        for part in seg.split("|"):
            _parse_segment(part.strip(), flags, seen)
        return
    group = re.fullmatch(r"-([A-Za-z0-9]{2,})", seg)
    if group:
        for ch in group.group(1):
            _append_flag(flags, seen, f"-{ch}", None)
        return
    long_m = re.match(r"^(--[A-Za-z0-9][A-Za-z0-9._-]*)(?:=(.+))?$", seg)
    if long_m:
        _append_flag(flags, seen, long_m.group(1), long_m.group(2))
        return
    short_val = re.match(r"^(-[A-Za-z0-9])\s+(.+)$", seg)
    if short_val:
        _append_flag(flags, seen, short_val.group(1), short_val.group(2))
        return
    short_eq = re.match(r"^(-[A-Za-z0-9])=(.+)$", seg)
    if short_eq:
        _append_flag(flags, seen, short_eq.group(1), short_eq.group(2))
        return
    short = re.match(r"^(-[A-Za-z0-9])$", seg)
    if short:
        _append_flag(flags, seen, short.group(1), None)


def _parse_usage_flags(text: str) -> list[dict]:
    flags: list[dict] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.lower().startswith("usage:"):
            continue
        for inner in re.findall(r"\[([^\]]+)\]", line):
            _parse_segment(inner, flags, seen)
    return flags


def _synopsis(text: str, program: str) -> str:
    for raw in text.splitlines():
        s = raw.strip()
        if s.lower().startswith("usage:"):
            return s
    return program


def _build_bundle(tool: UsageTool, flags: list[dict], synopsis: str, captured_at: datetime) -> dict:
    subject_id = tool.program
    subject = StructuredSubject(
        subject_id=subject_id,
        subject_kind="tool",
        name=tool.program,
        family=tool.program,
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        provenance_claim_ids=[],
        created_at=captured_at,
        updated_at=captured_at,
    )
    caps: list[StructuredCapability] = [
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "existence", "command"),
            subject_id=subject_id,
            capability_type="existence",
            title=f"{tool.program} exists",
            detail=_validated_detail(
                {
                    "kind": "existence",
                    "command": tool.program,
                    "source_url": tool.source_url,
                    "usage_signature": synopsis,
                    "runtime_verified": True,
                    "synopsis": synopsis,
                }
            ),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99,
            confidence_band=ConfidenceBand.STRONG,
            risk_level=RiskLevel.NONE,
            created_at=captured_at,
            updated_at=captured_at,
        ),
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "invocation", "usage"),
            subject_id=subject_id,
            capability_type="invocation",
            title=f"{tool.program} invocation",
            detail=_validated_detail(
                {
                    "kind": "invocation",
                    "command": tool.program,
                    "source_url": tool.source_url,
                    "usage_signature": synopsis,
                    "synopsis": synopsis,
                    "argv_schema": {"program": tool.program, "subcommand_path": [], "positionals": []},
                    "flag_schema": flags,
                }
            ),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99,
            confidence_band=ConfidenceBand.STRONG,
            risk_level=RiskLevel.LOW,
            created_at=captured_at,
            updated_at=captured_at,
        ),
    ]
    for flag in flags:
        caps.append(
            StructuredCapability(
                capability_id=_stable_row_id(subject_id, "configuration", flag["name"]),
                subject_id=subject_id,
                capability_type="configuration",
                title=f"{tool.program} flag {flag['name']}",
                detail=_validated_detail(
                    {
                        "kind": "configuration",
                        "command": tool.program,
                        "source_url": tool.source_url,
                        "usage_signature": synopsis,
                        "flag": flag,
                    }
                ),
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.98,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.NONE,
                created_at=captured_at,
                updated_at=captured_at,
            )
        )
    constraints = [
        StructuredConstraint(
            constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
            subject_id=subject_id,
            constraint_kind="environment",
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            detail={
                "command": tool.program,
                "source_url": tool.source_url,
                "requires_binary": tool.program,
                "runtime_verified": True,
            },
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]
    kind, destr, rev, mut, cost, expose, reason = tool.effect
    effects = [
        StructuredEffect(
            effect_id=_stable_row_id(subject_id, "effect", kind),
            subject_id=subject_id,
            effect_kind=kind,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            destructive=destr,
            reversible=rev,
            mutates_remote_state=mut,
            may_cost_money=cost,
            may_expose_secrets=expose,
            detail={
                "command": tool.program,
                "source_url": tool.source_url,
                "classification_reason": reason,
            },
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]
    return {"subject": subject, "capabilities": caps, "constraints": constraints, "effects": effects}


_REGISTRY: dict[str, UsageTool] = {
    "awk": UsageTool(
        program="awk",
        help_argv=("awk",),
        source_url="https://man.openbsd.org/awk",
        effect=("compute", False, True, False, False, False,
                "`awk` evaluates pattern-action programs against input text and writes results "
                "to stdout; no network or remote side effects."),
    ),
    "sed": UsageTool(
        program="sed",
        help_argv=("sed", "-h"),
        source_url="https://man.openbsd.org/sed",
        effect=("filesystem", False, True, False, False, False,
                "`sed` transforms input text streams and may edit local files in place with "
                "`-i`; no network or remote side effects."),
    ),
    "ssh": UsageTool(
        program="ssh",
        help_argv=("ssh", "-h"),
        source_url="https://man.openbsd.org/ssh",
        effect=("network", False, True, True, False, True,
                "`ssh` connects to remote hosts, may authenticate with keys or passwords, and "
                "can execute remote commands or tunnel network traffic."),
    ),
    "rsync": UsageTool(
        program="rsync",
        help_argv=("rsync", "--help"),
        source_url="https://download.samba.org/pub/rsync/rsync.1",
        effect=("network", False, True, True, False, False,
                "`rsync` copies and synchronizes local or remote filesystems and may overwrite "
                "destination data depending on the flags and direction used."),
    ),
}


def _ingest(tool: UsageTool, store, now: datetime) -> dict:
    text = _run_help(tool.help_argv)
    flags = _parse_usage_flags(text)
    bundle = _build_bundle(tool, flags, _synopsis(text, tool.program), now)
    if store is not None:
        store.upsert_subject_graph(
            bundle["subject"],
            capabilities=bundle["capabilities"],
            constraints=bundle["constraints"],
            effects=bundle["effects"],
        )
    return {"flags": len(flags), "capabilities": len(bundle["capabilities"])}


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
        r = _ingest(tool, store, now)
        print(
            f"{tool.program} usage ingest ({'dry-run' if args.dry_run else 'applied'}): "
            f"{r['flags']} flags, {r['capabilities']} caps"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
