from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "structured_ingest_prose_depth",
    ROOT / "tools" / "scripts" / "structured_ingest_prose_depth.py",
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules["structured_ingest_prose_depth"] = module
SPEC.loader.exec_module(module)


def test_load_seed_claims_supports_named_constants_and_fstrings(tmp_path: Path) -> None:
    seed = tmp_path / "seed_demo.py"
    seed.write_text(
        """
BASE = "https://example.com"
ALT = "/path"
CLAIMS = [
    ("demo-errors", "demo error: boom", "Fix: (1) run `demo --help`.", f"{BASE}{ALT}"),
]
"""
    )

    claims = module.load_seed_claims(seed)

    assert len(claims) == 1
    assert claims[0].source_url == "https://example.com/path"


def test_extracts_steps_and_commands_from_prose() -> None:
    statement = (
        "Problem. Fix: (1) inspect with `ssh -v host`; "
        "(2) load key: `ssh-add ~/.ssh/id_ed25519`; "
        "(3) retry connection."
    )

    assert module._extract_commands(statement) == [
        "ssh -v host",
        "ssh-add ~/.ssh/id_ed25519",
    ]
    assert module._extract_steps(statement) == [
        "inspect with `ssh -v host`",
        "load key: `ssh-add ~/.ssh/id_ed25519`",
        "retry connection",
    ]


def test_build_subject_graph_creates_workflow_subject() -> None:
    cfg = module.ToolSeedConfig(
        family="ssh",
        script_path=ROOT / "tools" / "scripts" / "seed_ssh_errors_recipes.py",
        alias_prefix="ssh",
    )
    claim = module.SeedClaim(
        tool_id="ssh-recipes",
        title="ssh recipe: local port forward",
        statement="Run `ssh -L 8080:host:80 user@host`; then verify with `curl localhost:8080`.",
        source_url="https://man.openbsd.org/ssh.1",
    )

    bundle = module._build_subject_graph(cfg, claim, module.datetime.now(module.timezone.utc))

    assert bundle["subject"].subject_kind == "workflow"
    assert bundle["subject"].family == "ssh"
    assert len(bundle["capabilities"]) >= 3
    assert len(bundle["constraints"]) >= 1
    assert len(bundle["effects"]) == 1
