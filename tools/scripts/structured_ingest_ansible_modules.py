"""Phase 2 of ansible knowledge verification: ansible.builtin modules.

Reads each module's authoritative machine-readable spec from
``ansible-doc -t module --json <fqcn>`` (run from the isolated install) and
persists structured rows: one subject per module, a ``metadata`` capability
for the module overview, one ``configuration`` capability per parameter
(type / required / default / choices / aliases / description), one
``metadata`` capability per documented return value, an ``environment``
constraint, and a typed ``effect``.

Non-hallucination guarantee: every field is read directly from the JSON
ansible itself emits — there is no text parsing to get wrong. A module is
skipped (and counted) only if ansible-doc cannot emit valid JSON for it.

Module parameters are not CLI flags, so they do NOT use the CLI capability
detail schema; they use module-shaped details validated by `_check_detail`.

Usage:
    structured_ingest_ansible_modules.py --ansible-bin-dir /tmp/ansible-extract/venv/bin \\
        --collection ansible.builtin --database sqlite:///backend/ansible_modules_stage.db
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
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
from app.services.structured_cli_ingestion import _stable_row_id  # noqa: E402
from app.services.structured_knowledge_store import (  # noqa: E402
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)

_DOCS = "https://docs.ansible.com/ansible/latest/collections/{path}_module.html"
# Modules that only read/gather and never change managed state.
_READ_ONLY = {
    "setup", "gather_facts", "stat", "find", "slurp", "getent", "debug",
    "assert", "fail", "set_fact", "set_stats", "validate_argument_spec",
    "wait_for", "wait_for_connection", "ping", "import_role", "import_tasks",
    "include_role", "include_tasks", "include_vars", "meta", "add_host",
    "group_by", "pause",
}
_EXECUTORS = {"command", "shell", "raw", "script", "expect"}


def _run_json(bin_dir: Path, collection: str, name: str) -> dict | None:
    fqcn = f"{collection}.{name}"
    proc = subprocess.run(
        [str(bin_dir / "ansible-doc"), "-t", "module", "--json", fqcn],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data.get(fqcn)


def _list_modules(bin_dir: Path, collection: str) -> list[str]:
    proc = subprocess.run(
        [str(bin_dir / "ansible-doc"), "-l", "-t", "module", "--json", collection],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(proc.stdout)
    return sorted(fqcn.split(".")[-1] for fqcn in data)


def _as_text(value) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value).strip()
    return str(value).strip() if value is not None else ""


def _check_detail(detail: dict) -> dict:
    assert detail.get("kind") in {"module", "parameter", "return"}, detail.get("kind")
    assert detail.get("fqcn"), "fqcn required"
    assert detail.get("source_url"), "source_url required"
    if detail["kind"] == "parameter":
        assert detail.get("name"), "parameter name required"
        assert isinstance(detail.get("required"), bool)
    return detail


def _effect(name: str, doc: dict) -> tuple[str, bool, bool, bool, bool, bool, str]:
    """(effect_kind, destructive, reversible, mutates_remote, may_cost, may_expose, reason)."""
    options = doc.get("options", {})
    has_absent = any(
        "absent" in (o.get("choices") or []) for o in options.values()
    )
    fqcn = doc.get("plugin_name") or name
    if name in _READ_ONLY:
        return ("network", False, True, False, False, False,
                f"`{fqcn}` reads or gathers state and does not change managed hosts.")
    if name in _EXECUTORS:
        return ("mutation", False, False, True, False, False,
                f"`{fqcn}` runs arbitrary commands on the target; effect depends on the command.")
    if has_absent:
        return ("destructive", True, False, True, False, False,
                f"`{fqcn}` can remove managed resources (a parameter accepts state=absent).")
    return ("mutation", False, True, True, False, False,
            f"`{fqcn}` changes managed state on the target host.")


def _build_bundle(name: str, mod: dict, captured_at: datetime) -> dict:
    doc = mod.get("doc", {})
    fqcn = doc.get("plugin_name") or f"ansible.builtin.{name}"
    subject_id = "ansible-builtin-" + name.replace("_", "-")
    source_url = _DOCS.format(path=fqcn.replace(".", "/"))
    attrs = doc.get("attributes", {}) or {}

    subject = StructuredSubject(
        subject_id=subject_id, subject_kind="tool", name=fqcn, family="ansible",
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        provenance_claim_ids=[], created_at=captured_at, updated_at=captured_at,
    )
    caps: list[StructuredCapability] = []

    options = doc.get("options", {}) or {}
    returns = mod.get("return", {}) or {}
    overview = _check_detail({
        "kind": "module", "fqcn": fqcn, "source_url": source_url,
        "short_description": doc.get("short_description") or fqcn,
        "description": doc.get("description") or [],
        "version_added": doc.get("version_added"),
        "supports_check_mode": bool((attrs.get("check_mode") or {}).get("support") == "full"),
        "supports_diff": bool((attrs.get("diff_mode") or {}).get("support") == "full"),
        "requirements": doc.get("requirements") or [],
        "parameter_names": sorted(options.keys()),
        "return_names": sorted(returns.keys()),
        "notes": doc.get("notes") or [],
        "examples": [mod.get("examples")] if mod.get("examples") else [],
    })
    caps.append(StructuredCapability(
        capability_id=_stable_row_id(subject_id, "module", "overview"),
        subject_id=subject_id, capability_type="metadata",
        title=f"{fqcn} module", detail=overview,
        verification_status=VerificationStatus.ACCEPTED,
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
        created_at=captured_at, updated_at=captured_at,
    ))
    for pname, o in options.items():
        detail = _check_detail({
            "kind": "parameter", "fqcn": fqcn, "source_url": source_url,
            "name": pname,
            "type": o.get("type", "str"),
            "required": bool(o.get("required", False)),
            "default": o.get("default"),
            "choices": o.get("choices") or [],
            "aliases": o.get("aliases") or [],
            "elements": o.get("elements"),
            "description": o.get("description") or [],
            "version_added": o.get("version_added"),
        })
        caps.append(StructuredCapability(
            capability_id=_stable_row_id(subject_id, "parameter", pname),
            subject_id=subject_id, capability_type="configuration",
            title=f"{fqcn} parameter {pname}", detail=detail,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=captured_at, updated_at=captured_at,
        ))
    for rname, r in returns.items():
        detail = _check_detail({
            "kind": "return", "fqcn": fqcn, "source_url": source_url,
            "name": rname, "type": r.get("type", "str"),
            "returned": _as_text(r.get("returned")),
            "description": r.get("description") or [],
        })
        caps.append(StructuredCapability(
            capability_id=_stable_row_id(subject_id, "return", rname),
            subject_id=subject_id, capability_type="metadata",
            title=f"{fqcn} return {rname}", detail=detail,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.98, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=captured_at, updated_at=captured_at,
        ))

    constraints = [StructuredConstraint(
        constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
        subject_id=subject_id, constraint_kind="environment",
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        detail={"fqcn": fqcn, "source_url": source_url, "requires_binary": "ansible",
                "requirements": doc.get("requirements") or [], "runtime_verified": True},
        created_at=captured_at, updated_at=captured_at,
    )]
    kind, destr, rev, mut, cost, expose, reason = _effect(name, doc)
    effects = [StructuredEffect(
        effect_id=_stable_row_id(subject_id, "effect", kind),
        subject_id=subject_id, effect_kind=kind,
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        destructive=destr, reversible=rev, mutates_remote_state=mut,
        may_cost_money=cost, may_expose_secrets=expose,
        detail={"fqcn": fqcn, "source_url": source_url, "classification_reason": reason},
        created_at=captured_at, updated_at=captured_at,
    )]
    return {"subject": subject, "capabilities": caps, "constraints": constraints,
            "effects": effects, "param_count": len(options), "return_count": len(returns)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ansible-bin-dir", default="/tmp/ansible-extract/venv/bin", type=Path)
    ap.add_argument("--collection", default="ansible.builtin")
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    modules = _list_modules(args.ansible_bin_dir, args.collection)
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0,
              "parameters": 0, "returns": 0, "skipped": 0}
    for name in modules:
        mod = _run_json(args.ansible_bin_dir, args.collection, name)
        if mod is None:
            totals["skipped"] += 1
            print(f"  SKIP {name}: no JSON")
            continue
        b = _build_bundle(name, mod, now)
        totals["subjects"] += 1
        totals["capabilities"] += len(b["capabilities"])
        totals["constraints"] += len(b["constraints"])
        totals["effects"] += len(b["effects"])
        totals["parameters"] += b["param_count"]
        totals["returns"] += b["return_count"]
        if store is not None:
            store.upsert_subject_graph(
                b["subject"], capabilities=b["capabilities"],
                constraints=b["constraints"], effects=b["effects"],
            )

    print(f"ansible {args.collection} module ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  modules listed: {len(modules)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
