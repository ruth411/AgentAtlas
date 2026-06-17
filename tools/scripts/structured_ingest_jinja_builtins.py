"""Phase 5b of ansible knowledge verification: Jinja2 built-in filters/tests.

ansible's templating relies on Jinja2's own built-in filters (``default``,
``join``, ``map``, …) and tests (``defined``, ``even``, …) that are NOT
ansible plugins and so are invisible to ``ansible-doc``. This script
introspects the *installed* Jinja2's actual registries
(``jinja2.filters.FILTERS`` / ``jinja2.tests.TESTS``) inside the isolated
venv — the authoritative, runtime source — and persists each as a structured
subject with its real signature, arguments, and docstring.

Non-hallucination: every field comes from Python introspection of the real
installed code (`inspect.signature` / `inspect.getdoc`), not from docs text.

Usage:
    structured_ingest_jinja_builtins.py --ansible-bin-dir /tmp/ansible-extract/venv/bin \\
        --database sqlite:///backend/jinja_stage.db
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
    StructuredKnowledgeStore,
    StructuredSubject,
)

_DOCS = "https://jinja.palletsprojects.com/en/stable/templates/#builtin-{kind}s"

# Introspection runs in the *isolated* venv so we read the exact Jinja2 ansible
# bundles. It skips the framework-injected leading params (context/environment)
# and the piped/tested value, leaving the real user-facing arguments.
_INTROSPECT = r"""
import json, inspect, jinja2
from jinja2.filters import FILTERS
from jinja2.tests import TESTS

def args_of(fn):
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return None, []
    params = [
        p for p in sig.parameters.values()
        if p.name not in {"eval_ctx", "context", "environment"}
        and "Context" not in str(p.annotation)
        and "Environment" not in str(p.annotation)
    ]
    params = params[1:]  # drop the piped/tested value itself
    args = []
    for p in params:
        default = p.default
        if default is inspect._empty or not isinstance(default, (str, int, float, bool, type(None))):
            default = None
        args.append({
            "name": p.name,
            "has_default": p.default is not inspect._empty,
            "default": default,
            "variadic": p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD),
            "type": (str(p.annotation) if p.annotation is not inspect._empty else None),
        })
    return str(sig), args

def extract(registry, kind):
    out = []
    for name, fn in registry.items():
        sig, args = args_of(fn)
        out.append({"name": name, "kind": kind, "signature": sig, "args": args,
                    "description": (inspect.getdoc(fn) or "").strip()})
    return out

print(json.dumps({"version": jinja2.__version__,
                  "filter": extract(FILTERS, "filter"),
                  "test": extract(TESTS, "test")}))
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ansible-bin-dir", default="/tmp/ansible-extract/venv/bin", type=Path)
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    proc = subprocess.run(
        [str(args.ansible_bin_dir / "python"), "-c", _INTROSPECT],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        print("introspection failed:", proc.stderr[-500:])
        return 1
    data = json.loads(proc.stdout)
    version = data["version"]

    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    totals = {"filter": 0, "test": 0, "capabilities": 0}

    for kind in ("filter", "test"):
        for entry in data[kind]:
            name = entry["name"]
            subject_id = f"ansible-jinja-{kind}-" + name.replace("_", "-")
            source_url = _DOCS.format(kind=kind)
            subject = StructuredSubject(
                subject_id=subject_id, subject_kind="subject",
                name=f"Jinja2 built-in {kind}: {name}", family="ansible",
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                provenance_claim_ids=[], created_at=now, updated_at=now,
            )
            overview = {
                "kind": f"jinja_{kind}", "name": name, "source_url": source_url,
                "provenance": "jinja2", "jinja2_version": version,
                "signature": entry["signature"], "description": entry["description"],
                "arg_names": [a["name"] for a in entry["args"]],
            }
            caps = [StructuredCapability(
                capability_id=_stable_row_id(subject_id, "jinja", name),
                subject_id=subject_id, capability_type="metadata",
                title=f"Jinja2 {kind} {name}", detail=overview,
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
                created_at=now, updated_at=now,
            )]
            for a in entry["args"]:
                caps.append(StructuredCapability(
                    capability_id=_stable_row_id(subject_id, "argument", a["name"]),
                    subject_id=subject_id, capability_type="configuration",
                    title=f"Jinja2 {kind} {name} argument {a['name']}",
                    detail={"kind": "argument", "name": a["name"], "source_url": source_url,
                            "fqcn": f"jinja2.{kind}.{name}", "type": a["type"],
                            "has_default": a["has_default"], "default": a["default"],
                            "variadic": a["variadic"]},
                    verification_status=VerificationStatus.ACCEPTED,
                    verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                    confidence=0.98, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
                    created_at=now, updated_at=now,
                ))
            constraint = StructuredConstraint(
                constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
                subject_id=subject_id, constraint_kind="environment",
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                detail={"name": name, "source_url": source_url, "requires_binary": "jinja2",
                        "jinja2_version": version, "runtime_verified": True},
                created_at=now, updated_at=now,
            )
            totals[kind] += 1
            totals["capabilities"] += len(caps)
            if store is not None:
                store.upsert_subject_graph(subject, capabilities=caps, constraints=[constraint], effects=[])

    print(f"jinja2 built-in ingest ({'dry-run' if args.dry_run else 'applied'}) "
          f"from jinja2 {version}: filters={totals['filter']}, tests={totals['test']}, "
          f"capabilities={totals['capabilities']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
