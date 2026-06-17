"""Deep, complete structured ingest of the entire `gh` command tree.

The original gh ingest used a fixed contract list (~74 commands). This walks
the *whole* tree recursively from `gh --help`, discovering every command at
every nesting depth (`gh pr create`, `gh project field-create`,
`gh secret delete`, …), runtime-verifies each via its real `--help`, and
persists typed rows using the existing gh parser.

A node is treated as a real command (and ingested) if its `--help` has its
own ``FLAGS`` section and/or a ``*COMMANDS`` section. Pure help topics
(``gh actions``, ``gh environment`` — only ``INHERITED FLAGS`` + prose) and
alias entries are skipped. Nothing is invented: every row comes from a real
`gh ... --help` run.

Usage:
    structured_ingest_gh_deep.py --database sqlite:///backend/gh_deep_stage.db [--dry-run]
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

from app.services.structured_cli_ingestion import (  # noqa: E402
    GhCommandSource,
    build_gh_subject_bundle,
    parse_gh_help,
    _subject_id_for_command,
)
from app.services.structured_knowledge_store import StructuredKnowledgeStore  # noqa: E402

_SECTION = re.compile(r"^([A-Z][A-Z ]+)$", re.MULTILINE)
_CMD_LINE = re.compile(r"^\s{2,}([a-z][a-z0-9-]+):\s")


def _run_help(command: tuple[str, ...]) -> str | None:
    proc = subprocess.run(
        [*command, "--help"], capture_output=True, text=True, timeout=20,
        env={"PATH": __import__("os").environ.get("PATH", ""), "GH_NO_UPDATE_NOTIFIER": "1"},
    )
    out = proc.stdout or proc.stderr
    return out if out.strip() else None


def _sections(text: str) -> set[str]:
    return set(_SECTION.findall(text))


def _help_topics(root_text: str) -> set[str]:
    """The authoritative set of gh help topics (non-commands), parsed from the
    `HELP TOPICS` section of `gh --help`. These are the only nodes that look
    like commands but aren't, so they're the only thing we exclude."""
    topics: set[str] = set()
    in_topics = False
    for line in root_text.splitlines():
        m_sec = re.match(r"^([A-Z][A-Z ]+)$", line.strip())
        if m_sec:
            in_topics = m_sec.group(1) == "HELP TOPICS"
            continue
        if in_topics:
            m = _CMD_LINE.match(line)
            if m:
                topics.add(m.group(1))
    return topics


def _has_usage(text: str) -> bool:
    # Every real gh command (even flagless/argless ones like `gh alias list`)
    # has a USAGE section; invalid nodes don't.
    return bool(re.search(r"^USAGE\b", text, re.MULTILINE))


def _subcommands(text: str) -> list[str]:
    subs: list[str] = []
    in_cmds = False
    for line in text.splitlines():
        st = line.strip()
        m_sec = re.match(r"^([A-Z][A-Z ]+)$", st)
        if m_sec:
            sec = m_sec.group(1)
            in_cmds = sec.endswith("COMMANDS") and "ALIAS" not in sec
            continue
        if in_cmds:
            m = _CMD_LINE.match(line)
            if m:
                subs.append(m.group(1))
    return subs


def _source_url(command: tuple[str, ...]) -> str:
    if command == ("gh",):
        return "https://cli.github.com/manual/gh"
    return "https://cli.github.com/manual/gh_" + "_".join(command[1:])


def walk(
    command: tuple[str, ...],
    seen: set[tuple[str, ...]],
    out: list[tuple],
    topics: set[str],
) -> None:
    if command in seen:
        return
    seen.add(command)
    # gh's HELP TOPICS are top-level non-commands; skip them, nothing else.
    if len(command) == 2 and command[1] in topics:
        return
    text = _run_help(command)
    if text is None or not _has_usage(text):
        return
    out.append((command, text))
    for sub in _subcommands(text):
        walk((*command, sub), seen, out, topics)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = _run_help(("gh",))
    topics = _help_topics(root) if root else set()
    discovered: list[tuple] = []
    walk(("gh",), set(), discovered, topics)

    store = None if args.dry_run else StructuredKnowledgeStore(database_url=args.database)
    now = datetime.now(timezone.utc)
    totals = {"subjects": 0, "capabilities": 0, "constraints": 0, "effects": 0}
    for command, text in discovered:
        parsed = parse_gh_help(command, text)
        source = GhCommandSource(
            subject_id=_subject_id_for_command(command),
            subject_name=" ".join(command),
            source_url=_source_url(command),
            command=command,
        )
        bundle = build_gh_subject_bundle(source, parsed, captured_at=now)
        totals["subjects"] += 1
        totals["capabilities"] += len(bundle["capabilities"])
        totals["constraints"] += len(bundle["constraints"])
        totals["effects"] += len(bundle["effects"])
        if store is not None:
            store.upsert_subject_graph(
                bundle["subject"], capabilities=bundle["capabilities"],
                constraints=bundle["constraints"], effects=bundle["effects"],
            )

    print(f"gh deep ingest ({'dry-run' if args.dry_run else 'applied'}):")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  commands discovered: {len(discovered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
