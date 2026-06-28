"""Structured ingest of kubectl and terraform — two cobra-adjacent trees whose
option formats are *not* the standard two-column flat list.

- kubectl: options are ``    -A, --all-namespaces=false:`` with the
  description on following tab-indented lines; the ``=<default>`` encodes the
  default value and type.
- terraform: options are two-column but use single-dash ``-flag=value`` form,
  so the value is normalized (``-flag=value`` → ``-flag value``) before the
  flat parser runs.

Both trees are discovered from their command listings and walked recursively.
Guarantees match the other ingesters: every flag traces to a parsed line,
unparseable option lines counted, caps L3, effects L2.

Usage:
    structured_ingest_kubectl_tf.py --tool kubectl   --database sqlite:///backend/ayiru_v0.2_bulk.db [--dry-run]
    structured_ingest_kubectl_tf.py --tool terraform --database sqlite:///backend/ayiru_v0.2_bulk.db
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
_CMD_LINE = re.compile(r"^\s{2,}([a-z][a-z0-9-]+)\s{2,}\S")
_DESTRUCTIVE = {"delete", "rm", "remove", "destroy", "prune", "uninstall", "purge", "taint"}


def _run(argv: list[str]) -> str:
    for tok in argv:
        if any(f in tok for f in _FORBIDDEN):
            raise RuntimeError(f"unsafe token: {tok!r}")
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return (p.stdout or "") + (p.stderr or "")


# ----------------------------------------------------------------- kubectl ----
def _kubectl_help(argv):
    return _run([*argv, "--help"])


def _kubectl_subcommands(text: str) -> list[str]:
    subs: list[str] = []
    in_cmds = False
    for line in text.splitlines():
        st = line.strip()
        if re.search(r"Commands.*:\s*$", st) and not line.startswith(" "):
            in_cmds = True
            continue
        if in_cmds:
            if not line.strip():
                continue
            if not line.startswith(" "):
                in_cmds = False
                continue
            m = _CMD_LINE.match(line)
            if m and m.group(1) not in ("help", "options"):
                subs.append(m.group(1))
    return subs


def _kubectl_value(default: str) -> tuple[bool, str | None, str, object]:
    """(takes_value, value_name, value_type, default) from kubectl's =<default>."""
    d = default.strip()
    if d in ("true", "false"):
        return False, None, "boolean", d == "true"
    if re.fullmatch(r"-?\d+", d):
        return True, "int", "integer", int(d)
    if re.fullmatch(r"\d+(\.\d+)?(s|m|h|ms)", d):
        return True, "duration", "duration", d
    if d == "[]":
        return True, "list", "string_list", None
    inner = d.strip("'")
    return True, "string", "string", (inner or None)


def _kubectl_flags(text: str) -> tuple[list[dict], int]:
    flags: list[dict] = []
    unparsed = 0
    in_opt = False
    cur: dict | None = None
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.rstrip()
        if re.match(r"^Options:", line):
            in_opt = True
            continue
        if re.match(r"^[A-Za-z].*:\s*$", line) and not line.startswith(" "):
            in_opt = re.match(r"^Options:", line) is not None
            cur = None
            continue
        if not in_opt:
            continue
        m = re.match(r"^\s{4}(?:(-[A-Za-z]),\s)?(--[\w-]+)=(.*):\s*$", line)
        if m:
            short, long, default = m.group(1), m.group(2), m.group(3)
            if long in seen:
                cur = None
                continue
            seen.add(long)
            takes, vname, vtype, dval = _kubectl_value(default)
            cur = {
                "name": long, "short": short, "takes_value": takes, "value_name": vname,
                "value_type": vtype, "repeatable": vtype == "string_list", "required": False,
                "deprecated": False, "inherited": False, "description": "",
                "default": dval, "choices": [],
            }
            flags.append(cur)
            continue
        if cur is not None and (line.startswith("\t") or re.match(r"^\s{6,}\S", line)):
            cur["description"] = (cur["description"] + " " + line.strip()).strip()
            continue
        if re.match(r"^\s{4}-", line):
            unparsed += 1
    for f in flags:
        if not f["description"]:
            f["description"] = f"{f['name']} option"
    return flags, unparsed


def _kubectl_effect(argv, leaf):
    c = " ".join(argv)
    if leaf in _DESTRUCTIVE:
        return ("destructive", True, False, True, False, False,
                f"`{c}` deletes Kubernetes resources from the cluster.")
    if leaf in {"apply", "create", "run", "expose", "scale", "edit", "patch", "replace",
                "set", "rollout", "label", "annotate", "cordon", "drain", "taint", "cp"}:
        return ("mutation", False, True, True, False, False,
                f"`{c}` changes cluster state.")
    if leaf in {"exec", "attach", "port-forward", "proxy"}:
        return ("network", False, True, False, False, False,
                f"`{c}` opens an interactive/streamed connection to the cluster.")
    return ("network", False, True, False, False, False,
            f"`{c}` reads cluster state without changing it.")


# --------------------------------------------------------------- terraform ----
def _tf_help(argv):
    # terraform uses single-dash -help.
    return _run([argv[0], *argv[1:], "-help"]) if len(argv) > 1 else _run([argv[0], "-help"])


def _tf_subcommands(text: str) -> list[str]:
    subs: list[str] = []
    grab = False
    for line in text.splitlines():
        st = line.strip()
        if re.match(r"^(Main commands|All other commands|Subcommands):\s*$", st):
            grab = True
            continue
        if grab:
            if not line.startswith(" ") and st:
                grab = False
                continue
            m = _CMD_LINE.match(line)
            if m and m.group(1) not in ("help",):
                subs.append(m.group(1))
    return subs


def _tf_flags(text: str) -> tuple[list[dict], int]:
    # Normalize "-flag=value" -> "-flag value" so the flat two-column parser
    # reads the value as a metavar, then reuse it.
    norm = re.sub(r"(?m)^(\s+-[A-Za-z0-9][\w-]*)=(\S+)", r"\1 \2", text)
    return flat._parse_flags(norm)


def _tf_effect(argv, leaf):
    c = " ".join(argv)
    if leaf in _DESTRUCTIVE:
        return ("destructive", True, False, True, False, False,
                f"`{c}` destroys or taints Terraform-managed infrastructure.")
    if leaf in {"apply", "import", "refresh", "state", "workspace", "init", "get", "login"}:
        net = leaf in {"apply", "import", "refresh", "init", "get", "login"}
        return ("mutation", False, True, net, False, leaf == "login",
                f"`{c}` changes infrastructure, state, or stored credentials" +
                (" and reaches the network." if net else "."))
    if leaf in {"plan", "validate", "output", "show", "graph", "providers", "version", "fmt", "console"}:
        return ("network", False, True, False, False, False,
                f"`{c}` reads or previews Terraform state/config without applying changes.")
    return ("network", False, True, False, False, False,
            f"`{c}` inspects Terraform state/config.")


_TOOLS = {
    "kubectl": {"family": "kubectl", "help": _kubectl_help, "subs": _kubectl_subcommands,
                "flags": _kubectl_flags, "effect": _kubectl_effect,
                "source_url": lambda a: "https://kubernetes.io/docs/reference/kubectl/generated/kubectl_"
                                        + "_".join(a[1:]) + "/" if len(a) > 1
                                        else "https://kubernetes.io/docs/reference/kubectl/"},
    "terraform": {"family": "terraform", "help": _tf_help, "subs": _tf_subcommands,
                  "flags": _tf_flags, "effect": _tf_effect,
                  "source_url": lambda a: "https://developer.hashicorp.com/terraform/cli/commands/"
                                          + "/".join(a[1:])},
}


def _synopsis(text: str, argv) -> str:
    for line in text.splitlines():
        if line.strip().lower().startswith("usage:"):
            return line.split(":", 1)[1].strip() or " ".join(argv)
    return " ".join(argv)


def _build(argv, family, flags, synopsis, source_url, effect, now):
    subject_id = "-".join(argv)
    command = " ".join(argv)
    subject = StructuredSubject(
        subject_id=subject_id, subject_kind="tool", name=command, family=family,
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED, provenance_claim_ids=[],
        created_at=now, updated_at=now,
    )
    caps = [
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "existence", "command"),
            subject_id=subject_id, capability_type="existence", title=f"{command} exists",
            detail=_validated_detail({
                "kind": "existence", "command": command, "source_url": source_url,
                "usage_signature": synopsis, "runtime_verified": True, "synopsis": synopsis}),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=now, updated_at=now),
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "invocation", "usage"),
            subject_id=subject_id, capability_type="invocation", title=f"{command} invocation",
            detail=_validated_detail({
                "kind": "invocation", "command": command, "source_url": source_url,
                "usage_signature": synopsis, "synopsis": synopsis,
                "argv_schema": {"program": argv[0], "subcommand_path": list(argv[1:]), "positionals": []},
                "flag_schema": flags}),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.LOW,
            created_at=now, updated_at=now),
    ]
    for flag in flags:
        caps.append(StructuredCapability(
            capability_id=_stable_row_id(subject_id, "configuration", flag["name"]),
            subject_id=subject_id, capability_type="configuration",
            title=f"{command} flag {flag['name']}",
            detail=_validated_detail({
                "kind": "configuration", "command": command, "source_url": source_url,
                "usage_signature": synopsis, "flag": flag}),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.98, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=now, updated_at=now))
    constraints = [StructuredConstraint(
        constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
        subject_id=subject_id, constraint_kind="environment",
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        detail={"command": command, "source_url": source_url, "requires_binary": argv[0],
                "runtime_verified": True}, created_at=now, updated_at=now)]
    kind, destr, rev, mut, cost, expose, reason = effect(argv, argv[-1])
    effects = [StructuredEffect(
        effect_id=_stable_row_id(subject_id, "effect", kind), subject_id=subject_id,
        effect_kind=kind, verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        destructive=destr, reversible=rev, mutates_remote_state=mut, may_cost_money=cost,
        may_expose_secrets=expose,
        detail={"command": command, "source_url": source_url, "classification_reason": reason},
        created_at=now, updated_at=now)]
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
            text = cfg["help"](argv)
        except Exception as exc:  # noqa: BLE001
            print(f"  SKIP {' '.join(argv)}: {exc}")
            continue
        if "usage:" not in text.lower():
            continue
        for sub in cfg["subs"](text):
            queue.append((*argv, sub))
        flags, unparsed = cfg["flags"](text)
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
                constraints=bundle["constraints"], effects=bundle["effects"])

    print(f"{args.tool} ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  nodes: {len(seen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
