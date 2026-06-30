from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flat_module = _load_module(
    "structured_ingest_flatcli",
    "tools/scripts/structured_ingest_flatcli.py",
)
subcmd_module = _load_module(
    "structured_ingest_subcmd",
    "tools/scripts/structured_ingest_subcmd.py",
)
usage_module = _load_module(
    "structured_ingest_usagecli",
    "tools/scripts/structured_ingest_usagecli.py",
)
vim_module = _load_module(
    "structured_ingest_vim_help",
    "tools/scripts/structured_ingest_vim_help.py",
)


def test_parse_optspec_expands_git_no_toggle() -> None:
    assert flat_module._parse_optspec("--[no-]dry-run") == [
        ("--dry-run", None),
        ("--no-dry-run", None),
    ]


def test_parse_optspec_expands_git_optional_value_toggle() -> None:
    assert flat_module._parse_optspec("--[no-]gpg-sign[=<key-id>]") == [
        ("--gpg-sign", "<key-id>"),
        ("--no-gpg-sign", None),
    ]


def test_git_subcommands_parses_help_a_listing(monkeypatch) -> None:
    sample = """
Main Porcelain Commands
   add                     Add file contents to the index
   commit                  Record changes to the repository

Ancillary Commands / Interrogators
   blame                   Show what revision last modified each line
"""

    monkeypatch.setattr(subcmd_module, "_run", lambda argv: sample)

    assert subcmd_module._git_subcommands() == ["add", "blame", "commit"]


def test_go_subcommands_parses_go_help_listing(monkeypatch) -> None:
    sample = """
The commands are:

\tbuild       compile packages and dependencies
\ttest        test packages
\tvet         report likely mistakes in packages

Use "go help <command>" for more information about a command.
"""

    monkeypatch.setattr(subcmd_module, "_run", lambda argv: sample)

    assert subcmd_module._go_subcommands() == ["build", "test", "vet"]


def test_usage_parser_extracts_compact_synopsis_flags() -> None:
    sample = """usage: awk [-F fs] [-v var=value] [-f progfile | 'prog'] [file ...]"""

    flags = usage_module._parse_usage_flags(sample)
    names = {flag["name"] for flag in flags}

    assert {"-F", "-v", "-f"} <= names


def test_usage_parser_expands_grouped_short_flags_and_long_options() -> None:
    sample = (
        "usage: rsync [-abc] [--cache | --no-cache] [--bwlimit=limit] "
        "[--max-size=SIZE] source ... directory"
    )

    flags = usage_module._parse_usage_flags(sample)
    by_name = {flag["name"]: flag for flag in flags}

    assert {"-a", "-b", "-c", "--cache", "--no-cache", "--bwlimit", "--max-size"} <= set(by_name)
    assert by_name["--bwlimit"]["takes_value"] is True
    assert by_name["--max-size"]["takes_value"] is True


def test_vim_quickref_parser_extracts_sections_and_commands() -> None:
    sample = """
*Q_lr*      Left-right motions
|h| N  h    left
|l| N  l    right
------------------------------------------------------------------------------
*Q_ud*      Up-down motions
|j| N  j    down N lines
"""

    sections = vim_module.parse_quickref(sample)

    assert [section.title for section in sections] == [
        "Left-right motions",
        "Up-down motions",
    ]
    assert [cmd.notation for cmd in sections[0].commands] == ["h", "l"]
