"""Lift preserved prose depth scripts into structured Ayiru rows.

Many families still have rich knowledge preserved in `seed_*_errors_recipes.py`
even though the current bulk DB is structured-only. This script parses those
local seed files and converts each error/recipe item into machine-readable
structured subjects, capabilities, constraints, and effects.

The conversion is intentionally explicit:
  - one seed claim becomes one structured subject
  - the prose body is split into steps
  - backticked command snippets become invocation examples
  - preconditions become structured constraints
  - the existing risk engine derives an effect profile

Usage:
  python tools/scripts/structured_ingest_prose_depth.py --tool ssh --database sqlite:///backend/ayiru_v0.2_bulk.db
  python tools/scripts/structured_ingest_prose_depth.py --tool all --database sqlite:///backend/ayiru_v0.2_bulk.db
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sys
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.schemas.enums import (  # noqa: E402
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.risk import RiskDimension  # noqa: E402
from app.services.confidence_scorer import band_for_score  # noqa: E402
from app.services.risk_classifier import classify_action  # noqa: E402
from app.services.structured_cli_ingestion import _stable_row_id, _validated_detail  # noqa: E402
from app.services.structured_knowledge_store import (  # noqa: E402
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)

_BACKTICK = re.compile(r"`([^`]+)`")
_FLAG = re.compile(r"(?<!\w)(--[A-Za-z0-9][A-Za-z0-9._-]*|-[A-Za-zA-Z0-9])(?!\w)")
_STEP_SPLIT = re.compile(r"\(\d+\)\s*")
_NON_ID = re.compile(r"[^a-z0-9]+")
_COMMAND_HEAD = re.compile(r"^[A-Za-z0-9./_:-][A-Za-z0-9./_:-]*$")


@dataclass(frozen=True)
class SeedClaim:
    tool_id: str
    title: str
    statement: str
    source_url: str


@dataclass(frozen=True)
class ToolSeedConfig:
    family: str
    script_path: Path
    alias_prefix: str
    filter_fn: Callable[[SeedClaim], bool] | None = None


def _safe_eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append(str(_safe_eval(value.value, env)))
            else:
                raise ValueError(f"Unsupported f-string node: {ast.dump(value)}")
        return "".join(parts)
    if isinstance(node, ast.List):
        return [_safe_eval(item, env) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval(item, env) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _safe_eval(node.left, env) + _safe_eval(node.right, env)
    raise ValueError(f"Unsupported AST node: {ast.dump(node)}")


def load_seed_claims(path: Path) -> list[SeedClaim]:
    tree = ast.parse(path.read_text(), filename=str(path))
    env: dict[str, Any] = {}
    claims_node: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "CLAIMS":
                        claims_node = node.value
                    else:
                        try:
                            env[target.id] = _safe_eval(node.value, env)
                        except Exception:
                            continue
    if claims_node is None:
        raise ValueError(f"No CLAIMS assignment found in {path}")
    rows = _safe_eval(claims_node, env)
    out: list[SeedClaim] = []
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 4:
            raise ValueError(f"Unexpected claim row in {path}: {row!r}")
        out.append(
            SeedClaim(
                tool_id=str(row[0]),
                title=str(row[1]),
                statement=str(row[2]),
                source_url=str(row[3]),
            )
        )
    return out


def _slug(text: str) -> str:
    text = _NON_ID.sub("-", text.casefold()).strip("-")
    return text[:72] or "item"


def _normalize_title(title: str, alias_prefix: str, family: str) -> str:
    lower = title.casefold()
    if lower.startswith(alias_prefix.casefold() + " "):
        return family + title[len(alias_prefix):]
    return title


def _kind_for_claim(claim: SeedClaim) -> tuple[str, str]:
    tid = claim.tool_id.casefold()
    title = claim.title.casefold()
    if "recipe" in tid or "recipe:" in title:
        return "workflow", "workflow"
    if "error" in tid or "warning" in title:
        return "subject", "metadata"
    return "subject", "metadata"


def _extract_commands(statement: str) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for snippet in _BACKTICK.findall(statement):
        cleaned = " ".join(snippet.strip().split())
        if not cleaned or len(cleaned) > 220:
            continue
        head = cleaned.split()[0]
        if not _COMMAND_HEAD.fullmatch(head):
            continue
        if not re.search(r"[A-Za-z0-9]", cleaned):
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        commands.append(cleaned)
        if len(commands) >= 8:
            break
    return commands


def _extract_steps(statement: str) -> list[str]:
    body = statement.split("Fix:", 1)[1] if "Fix:" in statement else statement
    body = body.replace("\n", " ")
    parts = _STEP_SPLIT.split(body)
    steps = [
        re.sub(r"\s+", " ", part).strip(" ;.")
        for part in parts
        if re.sub(r"\s+", " ", part).strip(" ;.")
    ]
    if not steps:
        fallback = [
            re.sub(r"\s+", " ", part).strip(" ;.")
            for part in re.split(r";\s+|\.\s+", body)
            if re.sub(r"\s+", " ", part).strip(" ;.")
        ]
        steps = fallback[:8]
    return steps[:12]


def _extract_preconditions(steps: list[str], commands: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for step in steps:
        lower = step.casefold()
        if any(token in lower for token in ("verify", "ensure", "must", "requires", "need", "check")):
            if step not in seen:
                seen.add(step)
                out.append(step)
    if commands:
        binaries = sorted({cmd.split()[0] for cmd in commands if cmd and not cmd.startswith("\\")})
        if binaries:
            phrase = f"Requires binaries or commands referenced in examples: {', '.join(binaries)}."
            out.append(phrase)
    return out[:8]


def _effect_kind_for_family(family: str) -> str:
    if family in {"awk", "jq"}:
        return "compute"
    if family in {"curl", "ssh", "rsync", "psql"}:
        return "network"
    return "filesystem"


def _effect_profile(subject: str, statement: str, family: str) -> tuple[str, bool, bool, bool, bool, bool, RiskLevel, list[str]]:
    assessment = classify_action(subject, statement, tool_id=family)
    dims = set(assessment.dimensions)
    if RiskDimension.DESTRUCTIVE in dims:
        kind = "destructive"
    elif RiskDimension.COST_INCURRING in dims:
        kind = "cost"
    elif RiskDimension.SECRET_EXPOSURE in dims:
        kind = "secret_exposure"
    elif RiskDimension.REMOTE_MUTATION in dims:
        kind = "mutation"
    elif RiskDimension.AUTH_SENSITIVE in dims or family in {"curl", "ssh", "rsync", "psql"}:
        kind = "network"
    elif RiskDimension.FILESYSTEM in dims:
        kind = "filesystem"
    else:
        kind = _effect_kind_for_family(family)
    destructive = RiskDimension.DESTRUCTIVE in dims
    remote_mutation = RiskDimension.REMOTE_MUTATION in dims
    may_cost = RiskDimension.COST_INCURRING in dims
    may_expose = RiskDimension.SECRET_EXPOSURE in dims or "password" in statement.casefold()
    return (
        kind,
        destructive,
        not destructive,
        remote_mutation or kind == "mutation",
        may_cost,
        may_expose,
        assessment.risk_level,
        list(assessment.reasons),
    )


def _command_detail(command: str) -> dict[str, Any]:
    flags = sorted(set(_FLAG.findall(command)))
    return {
        "command_example": command,
        "flags": flags,
        "tokens": command.split(),
    }


def _subject_id(family: str, kind: str, title: str) -> str:
    base = f"{family}-{kind}-{_slug(title)}"
    if len(base) <= 128:
        return base
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    return f"{family}-{kind}-{_slug(title)[:110]}-{digest}"[:128]


def _build_subject_graph(config: ToolSeedConfig, claim: SeedClaim, captured_at: datetime) -> dict[str, Any]:
    subject_kind, base_capability_type = _kind_for_claim(claim)
    normalized_title = _normalize_title(claim.title, config.alias_prefix, config.family)
    subject_id = _subject_id(
        config.family,
        "recipe" if subject_kind == "workflow" else "error",
        normalized_title,
    )
    commands = _extract_commands(claim.statement)
    steps = _extract_steps(claim.statement)
    preconditions = _extract_preconditions(steps, commands)
    effect_kind, destructive, reversible, mutates_remote, may_cost, may_expose, risk_level, reasons = _effect_profile(
        normalized_title,
        claim.statement,
        config.family,
    )

    subject = StructuredSubject(
        subject_id=subject_id,
        subject_kind=subject_kind,  # type: ignore[arg-type]
        name=normalized_title,
        family=config.family,
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        provenance_claim_ids=[],
        created_at=captured_at,
        updated_at=captured_at,
    )

    capabilities: list[StructuredCapability] = []
    summary_detail = {
        "kind": "workflow" if subject_kind == "workflow" else "metadata",
        "command": config.family,
        "source_url": claim.source_url,
        "title": normalized_title,
        "summary_text": claim.statement,
        "steps": steps,
        "commands": commands,
        "seed_tool_id": claim.tool_id,
        "imported_from": str(config.script_path.name),
    }
    summary_conf = 0.92 if subject_kind == "workflow" else 0.90
    capabilities.append(
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, base_capability_type, "summary"),
            subject_id=subject_id,
            capability_type=base_capability_type,  # type: ignore[arg-type]
            title=f"{normalized_title} {'workflow' if subject_kind == 'workflow' else 'guidance'}",
            detail=summary_detail,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=summary_conf,
            confidence_band=band_for_score(summary_conf),
            risk_level=risk_level,
            source="prose_projection",
            created_at=captured_at,
            updated_at=captured_at,
        )
    )

    if commands:
        capabilities.append(
            StructuredCapability(
                capability_id=_stable_row_id(subject_id, "metadata", "examples"),
                subject_id=subject_id,
                capability_type="metadata",
                title=f"{normalized_title} examples",
                detail={
                    "kind": "metadata",
                    "command": config.family,
                    "source_url": claim.source_url,
                    "aliases": [],
                    "commands": commands,
                    "step_count": len(steps),
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=0.88,
                confidence_band=band_for_score(0.88),
                risk_level=RiskLevel.NONE,
                source="prose_projection",
                created_at=captured_at,
                updated_at=captured_at,
            )
        )

    for idx, command in enumerate(commands[:6], start=1):
        capabilities.append(
            StructuredCapability(
                capability_id=_stable_row_id(subject_id, "invocation", f"cmd-{idx}"),
                subject_id=subject_id,
                capability_type="invocation",
                title=f"{normalized_title} example {idx}",
                detail=_validated_detail(
                    {
                        "kind": "invocation",
                        "source_url": claim.source_url,
                        "usage_signature": command,
                        "command": config.family,
                        "argv_schema": {
                            "program": config.family,
                            "subcommand_path": [],
                            "positionals": [],
                        },
                        "flag_schema": [],
                        "synopsis": command,
                    }
                ),
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=0.86,
                confidence_band=band_for_score(0.86),
                risk_level=risk_level,
                source="prose_projection",
                created_at=captured_at,
                updated_at=captured_at,
            )
        )

    constraints = [
        StructuredConstraint(
            constraint_id=_stable_row_id(subject_id, "constraint", "environment"),
            subject_id=subject_id,
            constraint_kind="environment",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            detail={
                "source_url": claim.source_url,
                "requires_binary": config.family,
                "seed_tool_id": claim.tool_id,
                "preconditions": preconditions,
            },
            source="prose_projection",
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]
    if preconditions:
        constraints.append(
            StructuredConstraint(
                constraint_id=_stable_row_id(subject_id, "constraint", "preconditions"),
                subject_id=subject_id,
                constraint_kind="precondition",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                detail={
                    "source_url": claim.source_url,
                    "preconditions": preconditions,
                },
                source="prose_projection",
                created_at=captured_at,
                updated_at=captured_at,
            )
        )

    effects = [
        StructuredEffect(
            effect_id=_stable_row_id(subject_id, "effect", effect_kind),
            subject_id=subject_id,
            effect_kind=effect_kind,
            destructive=destructive,
            reversible=reversible,
            mutates_remote_state=mutates_remote,
            may_cost_money=may_cost,
            may_expose_secrets=may_expose,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            detail={
                "source_url": claim.source_url,
                "classification_reason": "; ".join(reasons) if reasons else "Derived from prose depth item via risk engine.",
                "seed_tool_id": claim.tool_id,
            },
            source="prose_projection",
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]

    return {
        "subject": subject,
        "capabilities": capabilities,
        "constraints": constraints,
        "effects": effects,
    }


TOOLS: dict[str, ToolSeedConfig] = {
    "awk": ToolSeedConfig("awk", REPO_ROOT / "tools" / "scripts" / "seed_awk_errors_recipes.py", "awk"),
    "curl": ToolSeedConfig("curl", REPO_ROOT / "tools" / "scripts" / "seed_curl_errors_recipes.py", "curl"),
    "ffmpeg": ToolSeedConfig("ffmpeg", REPO_ROOT / "tools" / "scripts" / "seed_ffmpeg_errors_recipes.py", "ffmpeg"),
    "go": ToolSeedConfig("go", REPO_ROOT / "tools" / "scripts" / "seed_go_errors_recipes.py", "go"),
    "jq": ToolSeedConfig("jq", REPO_ROOT / "tools" / "scripts" / "seed_jq_errors_recipes.py", "jq"),
    "magick": ToolSeedConfig("magick", REPO_ROOT / "tools" / "scripts" / "seed_imagemagick_errors_recipes.py", "imagemagick"),
    "psql": ToolSeedConfig(
        "psql",
        REPO_ROOT / "tools" / "scripts" / "seed_postgresql_errors_recipes.py",
        "postgresql",
        filter_fn=lambda claim: (
            "app-psql" in claim.source_url.casefold()
            or "psql" in claim.title.casefold()
            or "psql" in claim.statement.casefold()
        ),
    ),
    "rsync": ToolSeedConfig("rsync", REPO_ROOT / "tools" / "scripts" / "seed_rsync_errors_recipes.py", "rsync"),
    "rustc": ToolSeedConfig("rustc", REPO_ROOT / "tools" / "scripts" / "seed_rust_errors_recipes.py", "rust"),
    "sed": ToolSeedConfig("sed", REPO_ROOT / "tools" / "scripts" / "seed_sed_errors_recipes.py", "sed"),
    "sqlite3": ToolSeedConfig("sqlite3", REPO_ROOT / "tools" / "scripts" / "seed_sqlite3_errors_recipes.py", "sqlite3"),
    "ssh": ToolSeedConfig("ssh", REPO_ROOT / "tools" / "scripts" / "seed_ssh_errors_recipes.py", "ssh"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True, help="tool family key or 'all'")
    parser.add_argument("--database", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    keys = sorted(TOOLS) if args.tool == "all" else [args.tool]
    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)

    for key in keys:
        cfg = TOOLS[key]
        claims = load_seed_claims(cfg.script_path)
        if cfg.filter_fn is not None:
            claims = [claim for claim in claims if cfg.filter_fn(claim)]
        written = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0}
        for claim in claims:
            bundle = _build_subject_graph(cfg, claim, now)
            written["subjects"] += 1
            written["capabilities"] += len(bundle["capabilities"])
            written["constraints"] += len(bundle["constraints"])
            written["effects"] += len(bundle["effects"])
            if store is not None:
                store.upsert_subject_graph(
                    bundle["subject"],
                    capabilities=bundle["capabilities"],
                    constraints=bundle["constraints"],
                    effects=bundle["effects"],
                )
        print(
            f"{key} prose depth ingest ({'dry-run' if args.dry_run else 'applied'}): "
            f"{written['subjects']} subjects, {written['capabilities']} caps, "
            f"{written['constraints']} constraints, {written['effects']} effects"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
