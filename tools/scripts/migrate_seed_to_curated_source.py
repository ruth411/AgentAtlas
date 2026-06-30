"""Convert legacy seed scripts into canonical machine-readable source files."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.schemas.enums import RiskLevel  # noqa: E402
from app.services.curated_tool_source import curated_tool_source_validator  # noqa: E402


def _load_prose_depth_module():
    script_path = REPO_ROOT / "tools" / "scripts" / "structured_ingest_prose_depth.py"
    spec = importlib.util.spec_from_file_location("structured_ingest_prose_depth", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["structured_ingest_prose_depth"] = module
    spec.loader.exec_module(module)
    return module


def build_document(tool: str, source_module: Any) -> dict[str, Any]:
    cfg = source_module.TOOLS[tool]
    claims = source_module.load_seed_claims(cfg.script_path)
    if cfg.filter_fn is not None:
        claims = [claim for claim in claims if cfg.filter_fn(claim)]
    try:
        seed_script_path = str(cfg.script_path.relative_to(REPO_ROOT))
    except ValueError:
        seed_script_path = str(cfg.script_path)

    artifact_id = f"{cfg.family}-curated-source-v1"
    entries: list[dict[str, Any]] = []
    for claim in claims:
        subject_kind, _ = source_module._kind_for_claim(claim)
        title = source_module._normalize_title(claim.title, cfg.alias_prefix, cfg.family)
        entry_id = source_module._subject_id(
            cfg.family,
            "recipe" if subject_kind == "workflow" else "error",
            title,
        )
        commands = source_module._extract_commands(claim.statement)
        steps = source_module._extract_steps(claim.statement)
        constraints = source_module._extract_preconditions(steps, commands)
        (
            effect_kind,
            destructive,
            reversible,
            mutates_remote_state,
            may_cost_money,
            may_expose_secrets,
            risk_level,
            reasons,
        ) = source_module._effect_profile(title, claim.statement, cfg.family)
        confidence = 0.92 if subject_kind == "workflow" else 0.90
        entries.append(
            {
                "entry_id": entry_id,
                "kind": "recipe" if subject_kind == "workflow" else "error",
                "title": title,
                "statement": claim.statement,
                "source_url": claim.source_url,
                "source_type": "official_docs",
                "commands": commands,
                "steps": steps,
                "constraints": constraints,
                "effect": {
                    "kind": effect_kind,
                    "destructive": destructive,
                    "reversible": reversible,
                    "mutates_remote_state": mutates_remote_state,
                    "may_cost_money": may_cost_money,
                    "may_expose_secrets": may_expose_secrets,
                    "risk_level": RiskLevel(risk_level).value,
                    "reasons": reasons,
                },
                "provenance": {
                    "source_artifact_id": artifact_id,
                    "migration_method": "legacy_seed_projection_v1",
                    "legacy_seed_script": seed_script_path,
                    "legacy_tool_id": claim.tool_id,
                    "legacy_title": claim.title,
                },
                "confidence": confidence,
            }
        )

    document = {
        "family": cfg.family,
        "version": "v1",
        "artifact_id": artifact_id,
        "generated_from": {
            "kind": "legacy_seed_script",
            "path": seed_script_path,
        },
        "entries": entries,
    }
    curated_tool_source_validator().validate(document)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source_module = _load_prose_depth_module()
    document = build_document(args.tool, source_module)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2) + "\n")
    print(f"Wrote {output_path} with {len(document['entries'])} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
