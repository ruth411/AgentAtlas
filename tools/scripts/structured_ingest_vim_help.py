"""Structured ingest for local Vim help docs.

Parses the built-in `quickref.txt` shipped with Vim and projects its sections
and command summaries into structured Ayiru rows.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.schemas.enums import ConfidenceBand, RiskLevel, VerificationLevel, VerificationStatus  # noqa: E402
from app.services.structured_cli_ingestion import _stable_row_id  # noqa: E402
from app.services.structured_knowledge_store import (  # noqa: E402
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)

DOC_PATH = Path("/usr/share/vim/vim91/doc/quickref.txt")
SOURCE_URL = f"file://{DOC_PATH}"
_SECTION_RE = re.compile(r"^\*(Q_[^*]+)\*\s+(.+?)\s*$")
_COMMAND_RE = re.compile(r"^\|([^|]+)\|\s+(.*\S)\s*$")
_NON_ID = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class VimCommand:
    tag: str
    notation: str
    action: str


@dataclass(frozen=True)
class VimSection:
    tag: str
    title: str
    commands: list[VimCommand]


def _slug(text: str) -> str:
    return _NON_ID.sub("-", text.casefold()).strip("-") or "item"


def _parse_command(rest: str) -> VimCommand | None:
    parts = [part.strip() for part in re.split(r"\t+|\s{2,}", rest) if part.strip()]
    if len(parts) < 2:
        return None
    notation = parts[1] if len(parts) >= 3 else parts[0]
    action = parts[2] if len(parts) >= 3 else parts[1]
    if not notation or not action:
        return None
    return VimCommand(tag=parts[0], notation=notation, action=action)


def parse_quickref(text: str) -> list[VimSection]:
    sections: list[VimSection] = []
    current_tag: str | None = None
    current_title: str | None = None
    current_commands: list[VimCommand] = []

    def flush() -> None:
        nonlocal current_tag, current_title, current_commands
        if current_tag and current_title and current_commands:
            sections.append(
                VimSection(
                    tag=current_tag,
                    title=current_title,
                    commands=current_commands,
                )
            )
        current_tag = None
        current_title = None
        current_commands = []

    for raw in text.splitlines():
        line = raw.rstrip()
        heading = _SECTION_RE.match(line)
        if heading:
            flush()
            current_tag = heading.group(1)
            current_title = heading.group(2)
            continue
        if current_tag is None:
            continue
        if line.startswith("------------------------------------------------------------------------------"):
            continue
        cmd_match = _COMMAND_RE.match(line)
        if not cmd_match:
            continue
        parsed = _parse_command(cmd_match.group(2))
        if parsed is not None:
            current_commands.append(parsed)
    flush()
    return sections


def _build_bundle(section: VimSection, captured_at: datetime) -> dict[str, object]:
    subject_id = f"vim-{_slug(section.title)}"
    subject = StructuredSubject(
        subject_id=subject_id,
        subject_kind="subject",
        name=f"vim {section.title}",
        family="vim",
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        provenance_claim_ids=[],
        created_at=captured_at,
        updated_at=captured_at,
    )
    capabilities: list[StructuredCapability] = [
        StructuredCapability(
            capability_id=_stable_row_id(subject_id, "metadata", "summary"),
            subject_id=subject_id,
            capability_type="metadata",
            title=f"vim {section.title} summary",
            detail={
                "kind": "metadata",
                "command": "vim",
                "source_url": f"{SOURCE_URL}#{section.tag}",
                "aliases": [],
                "section_tag": section.tag,
                "section_title": section.title,
                "entry_count": len(section.commands),
            },
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=0.95,
            confidence_band=ConfidenceBand.STRONG,
            risk_level=RiskLevel.NONE,
            source="structured_ingestion",
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]
    for idx, command in enumerate(section.commands, start=1):
        capabilities.append(
            StructuredCapability(
                capability_id=_stable_row_id(subject_id, "metadata", f"cmd-{idx}"),
                subject_id=subject_id,
                capability_type="metadata",
                title=f"vim {section.title}: {command.notation}",
                detail={
                    "kind": "metadata",
                    "command": "vim",
                    "source_url": f"{SOURCE_URL}#{section.tag}",
                    "aliases": [],
                    "section_tag": section.tag,
                    "section_title": section.title,
                    "help_tag": command.tag,
                    "notation": command.notation,
                    "action": command.action,
                },
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=0.93,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.LOW,
                source="structured_ingestion",
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
                "source_url": f"{SOURCE_URL}#{section.tag}",
                "requires_binary": "vim",
                "local_doc_path": str(DOC_PATH),
            },
            source="structured_ingestion",
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]
    effects = [
        StructuredEffect(
            effect_id=_stable_row_id(subject_id, "effect", "filesystem"),
            subject_id=subject_id,
            effect_kind="filesystem",
            destructive=False,
            reversible=True,
            mutates_remote_state=False,
            may_cost_money=False,
            may_expose_secrets=False,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            detail={
                "source_url": f"{SOURCE_URL}#{section.tag}",
                "classification_reason": "Vim keybindings and editing actions primarily affect local buffers and files.",
            },
            source="structured_ingestion",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sections = parse_quickref(DOC_PATH.read_text())
    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    written = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0}
    for section in sections:
        bundle = _build_bundle(section, now)
        written["subjects"] += 1
        written["capabilities"] += len(bundle["capabilities"])  # type: ignore[arg-type]
        written["constraints"] += len(bundle["constraints"])  # type: ignore[arg-type]
        written["effects"] += len(bundle["effects"])  # type: ignore[arg-type]
        if store is not None:
            store.upsert_subject_graph(
                bundle["subject"],  # type: ignore[arg-type]
                capabilities=bundle["capabilities"],  # type: ignore[arg-type]
                constraints=bundle["constraints"],  # type: ignore[arg-type]
                effects=bundle["effects"],  # type: ignore[arg-type]
            )
    print(
        f"vim quickref ingest ({'dry-run' if args.dry_run else 'applied'}): "
        f"{written['subjects']} subjects, {written['capabilities']} caps, "
        f"{written['constraints']} constraints, {written['effects']} effects"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
