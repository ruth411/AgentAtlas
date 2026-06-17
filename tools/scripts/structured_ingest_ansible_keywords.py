"""Phase 5a of ansible knowledge verification: playbook keywords.

Reads the runtime keyword reference from ``ansible-doc -t keyword --json``
(``when``, ``loop``, ``become``, ``notify``, …) and persists one subject per
keyword with a typed ``metadata`` capability: which playbook objects it
applies to, its value type, whether it is Jinja-templated implicitly or
explicitly, its precedence priority, and its description.

Keywords are playbook directives, not actions, so they carry no managed-host
effect. Every field is read directly from ansible's JSON output (L3).

Usage:
    structured_ingest_ansible_keywords.py --ansible-bin-dir /tmp/ansible-extract/venv/bin \\
        --database sqlite:///backend/ansible_keywords_stage.db
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

_DOCS = "https://docs.ansible.com/ansible/latest/reference_appendices/playbooks_keywords.html"


def _list_keywords(bin_dir: Path) -> dict:
    proc = subprocess.run(
        [str(bin_dir / "ansible-doc"), "-t", "keyword", "-l", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    return json.loads(proc.stdout)


def _keyword_json(bin_dir: Path, name: str) -> dict | None:
    proc = subprocess.run(
        [str(bin_dir / "ansible-doc"), "-t", "keyword", "--json", name],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout).get(name)
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ansible-bin-dir", default="/tmp/ansible-extract/venv/bin", type=Path)
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    names = sorted(_list_keywords(args.ansible_bin_dir))
    written = skipped = 0
    for name in names:
        kw = _keyword_json(args.ansible_bin_dir, name)
        if kw is None:
            skipped += 1
            continue
        subject_id = "ansible-keyword-" + name.replace("_", "-")
        subject = StructuredSubject(
            subject_id=subject_id, subject_kind="subject", name=f"ansible playbook keyword: {name}",
            family="ansible", verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            provenance_claim_ids=[], created_at=now, updated_at=now,
        )
        detail = {
            "kind": "keyword", "name": name, "source_url": _DOCS,
            "applies_to": kw.get("applies_to") or [],
            "type": kw.get("type"),
            "template": kw.get("template"),
            "priority": kw.get("priority"),
            "description": kw.get("description") or "",
        }
        cap = StructuredCapability(
            capability_id=_stable_row_id(subject_id, "keyword", name),
            subject_id=subject_id, capability_type="metadata",
            title=f"playbook keyword {name}", detail=detail,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99, confidence_band=ConfidenceBand.STRONG, risk_level=RiskLevel.NONE,
            created_at=now, updated_at=now,
        )
        constraint = StructuredConstraint(
            constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
            subject_id=subject_id, constraint_kind="environment",
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            detail={"keyword": name, "source_url": _DOCS, "requires_binary": "ansible",
                    "runtime_verified": True},
            created_at=now, updated_at=now,
        )
        written += 1
        if store is not None:
            store.upsert_subject_graph(subject, capabilities=[cap], constraints=[constraint], effects=[])

    print(f"ansible keyword ingest ({'dry-run' if args.dry_run else 'applied'}): "
          f"{written} keywords, {skipped} skipped, {len(names)} listed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
