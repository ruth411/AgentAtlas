"""Structured ingest of cobra-style command trees (helm, docker, ...).

These tools expose a recursive command tree (``helm repo add``,
``docker container run``) and print each command's options in the flat
two-column form (``Flags:`` / ``Options:`` / ``Global Flags:``) the flat
parser already understands. This walker discovers the whole tree from the
``*Commands:`` sections of each ``--help``, runtime-verifies every node, and
reuses ``structured_ingest_flatcli._parse_flags`` for the options.

Guarantees match the other ingesters: every flag traces to a parsed line,
unparseable option lines counted, caps L3, effects L2.

Usage:
    structured_ingest_cobra.py --tool helm   --database sqlite:///backend/ayiru_v0.2_bulk.db [--dry-run]
    structured_ingest_cobra.py --tool docker --database sqlite:///backend/ayiru_v0.2_bulk.db
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
_CMD_SECTION = re.compile(r"^(?:[A-Za-z][\w ]*\s)?Commands:\s*$")
_CMD_LINE = re.compile(r"^\s{2,}([a-z][a-z0-9-]+)\*?\s{2,}\S")
_DESTRUCTIVE = {"uninstall", "delete", "rm", "rmi", "remove", "prune", "destroy", "purge"}


def _run_help(argv: tuple[str, ...]) -> str:
    cmd = [*argv, "--help"]
    for tok in cmd:
        if any(f in tok for f in _FORBIDDEN):
            raise RuntimeError(f"unsafe token: {tok!r}")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return (p.stdout or "") + (p.stderr or "")


def _subcommands(text: str) -> list[str]:
    subs: list[str] = []
    in_cmds = False
    for line in text.splitlines():
        if _CMD_SECTION.match(line.strip()) and not line.startswith(" "):
            in_cmds = True
            continue
        if in_cmds:
            if not line.strip():
                continue
            if not line.startswith(" "):  # next section header
                in_cmds = False
                continue
            m = _CMD_LINE.match(line)
            if m and m.group(1) not in ("help",):
                subs.append(m.group(1))
    return subs


def _synopsis(text: str, argv: tuple[str, ...]) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("usage:"):
            rest = line.split(":", 1)[1].strip()
            return rest or (lines[i + 1].strip() if i + 1 < len(lines) else " ".join(argv))
    return " ".join(argv)


# ----------------------------------------------------------- effect models ----
def _helm_effect(argv, leaf):
    c = " ".join(argv)
    if leaf in _DESTRUCTIVE:
        return ("destructive", True, True, False, False, False,
                f"`{c}` removes a Helm release or local artifact.")
    if leaf in {"install", "upgrade", "rollback"} or argv[-2:] == ("repo", "add"):
        return ("mutation", False, True, True, False, False,
                f"`{c}` changes cluster or local Helm state and may reach the network.")
    if leaf in {"add", "update", "pull", "push", "fetch", "login", "logout"}:
        return ("network", False, True, False, False, leaf in {"login", "logout"},
                f"`{c}` reaches a chart repository or registry over the network.")
    return ("network", False, True, False, False, False,
            f"`{c}` reads Helm/cluster state without destructive changes.")


def _docker_effect(argv, leaf):
    c = " ".join(argv)
    if leaf in _DESTRUCTIVE or leaf in {"kill", "stop"}:
        destr = leaf in _DESTRUCTIVE
        return ("destructive" if destr else "mutation", destr, not destr or leaf != "rmi",
                False, False, False,
                f"`{c}` removes or terminates Docker resources.")
    if leaf in {"login", "logout"}:
        return ("secret_exposure", False, True, False, False, True,
                f"`{c}` handles Docker registry credentials.")
    if leaf in {"pull", "push", "build", "run", "create", "start", "commit", "load", "import", "exec", "tag", "save"}:
        net = leaf in {"pull", "push", "build", "run", "create"}
        return ("mutation", False, True, False, False, False,
                f"`{c}` creates or changes Docker resources" + (" and may reach the network." if net else "."))
    return ("network", False, True, False, False, False,
            f"`{c}` reads Docker state without destructive changes.")


_TOOLS = {
    "helm": {"family": "helm", "effect": _helm_effect,
             "source_url": lambda a: "https://helm.sh/docs/helm/" + "_".join(a) + "/"},
    "docker": {"family": "docker", "effect": _docker_effect,
               "source_url": lambda a: "https://docs.docker.com/reference/cli/" + "/".join(a) + "/"},
}


def _build(argv, family, flags, synopsis, source_url, effect, captured_at):
    subject_id = "-".join(argv)
    command = " ".join(argv)
    program = argv[0]
    subject = StructuredSubject(
        subject_id=subject_id, subject_kind="tool", name=command, family=family,
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED, provenance_claim_ids=[],
        created_at=captured_at, updated_at=captured_at,
    )
    caps = [
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
                "argv_schema": {"program": program, "subcommand_path": list(argv[1:]), "positionals": []},
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
    kind, destr, rev, mut, cost, expose, reason = effect(argv, argv[-1])
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
    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0, "unparsed": 0}

    queue: list[tuple[str, ...]] = [(args.tool,)]
    seen: set[tuple[str, ...]] = set()
    while queue:
        argv = queue.pop(0)
        if argv in seen:
            continue
        seen.add(argv)
        try:
            text = _run_help(argv)
        except Exception as exc:  # noqa: BLE001
            print(f"  SKIP {' '.join(argv)}: {exc}")
            continue
        if "usage:" not in text.lower():
            continue
        for sub in _subcommands(text):
            queue.append((*argv, sub))
        flags, unparsed = flat._parse_flags(text)
        bundle = _build(argv, cfg["family"], flags, _synopsis(text, argv),
                        cfg["source_url"](argv), cfg["effect"], now)
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

    print(f"{args.tool} cobra ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  nodes: {len(seen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
