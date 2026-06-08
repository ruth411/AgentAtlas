"""Stage 11a: Seed script smoke test.

One focused end-to-end test that runs `scripts/seed_examples.py --reset`
against a temp SQLite database and asserts:

- the script exits cleanly (returncode == 0)
- a useful number of claims land in the graph (>=30 — enough for the
  dashboard to look populated)
- every headline demo scenario is discoverable via the matcher (the
  pre-captured-artifact strategy means these queries MUST work on a
  fresh seed; if they don't, the demo video would be broken)
- the critical-risk verdict shape is intact (`gh repo delete` blocks
  auto-execution with the right reasons)

Hermetic: spawns the script as a subprocess against a tmp DB so the
test never touches the dev SQLite file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.services.claim_store import ClaimStore
from app.services.query_engine import QueryEngine


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "seed_examples.py"


@pytest.fixture
def seeded_store(tmp_path) -> ClaimStore:
    """Run the seed script against a fresh tmp DB. The fixture returns a
    ClaimStore pointed at the same DB so individual tests can verify the
    persisted state."""
    db_path = tmp_path / "seed.db"
    database_url = f"sqlite:///{db_path}"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--database-url",
            database_url,
            "--reset",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"seed_examples.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return ClaimStore(database_url=database_url)


# ---------------------------------------------------------------------------
# Offline-DNS shim
# ---------------------------------------------------------------------------


def test_offline_seed_dns_only_pins_seed_hosts(monkeypatch) -> None:
    """The offline-DNS shim pins only the known seed hosts; every other
    lookup delegates to the real resolver, so it can't silently redirect an
    unrelated fetch to a placeholder IP (P3-4)."""
    import socket

    from app.seed_data.runner import _offline_seed_dns

    delegated: list[str] = []

    def sentinel(host, port, *args, **kwargs):
        delegated.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", port))]

    monkeypatch.setattr(socket, "getaddrinfo", sentinel)

    with _offline_seed_dns():
        pinned = socket.getaddrinfo("api.openai.com", 443)
        other = socket.getaddrinfo("example.org", 443)

    assert pinned[0][4][0] == "93.184.216.34"  # seed host pinned
    assert other[0][4][0] == "1.2.3.4"  # non-seed host delegated
    assert delegated == ["example.org"]
    assert socket.getaddrinfo is sentinel  # original restored on exit


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_seed_produces_useful_claim_count(seeded_store: ClaimStore) -> None:
    """The seeded graph must have at least 30 claims so the dashboard's
    'list tools' page doesn't look empty."""
    all_claims = seeded_store.list(limit=500)
    assert len(all_claims) >= 30


def test_seed_covers_multiple_stage_0_tools(seeded_store: ClaimStore) -> None:
    """The headline demo scenarios span 4 of the 5 Stage 0 tools. Confirm
    all of them have at least one claim in the graph after seeding."""
    for tool_id in ("git", "github-cli", "docker", "openai-api", "vercel-cli"):
        claims = seeded_store.list(tool_id=tool_id, limit=50)
        assert claims, f"no claims found for {tool_id}"


def test_seed_publishes_canonical_tool_specs(seeded_store: ClaimStore) -> None:
    """The seed must publish a canonical ToolSpec for every tool whose
    headline-scenario claims reach ACCEPTED status. Without this step,
    `search_tools` and `get_tool_spec` return empty/404 and four of the
    six MCP query tools are useless out of the box — which defeats the
    whole point of seeding a demo graph.

    The 4 headline tools (`git`, `github-cli`, `docker`, `vercel-cli`)
    must publish; `openai-api`'s OpenAPI-derived claims stay in pending
    review so we don't assert a spec for it."""
    expected_published = {"git", "github-cli", "docker", "vercel-cli"}
    for tool_id in expected_published:
        spec = seeded_store.get_canonical_tool_spec(tool_id)
        assert spec is not None, (
            f"no canonical ToolSpec published for {tool_id!r} after seed; "
            "agent-facing search_tools / get_tool_spec endpoints will "
            "return empty results."
        )
        assert spec.capabilities, (
            f"{tool_id!r} canonical spec has no capabilities; agents "
            "searching by capability substring will miss this tool."
        )


# ---------------------------------------------------------------------------
# Headline demo scenarios — these MUST resolve correctly after a fresh seed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_id,command,expected_risk",
    [
        ("github-cli", "gh repo delete my-org/x --yes", "critical"),
        ("git", "git status", "low"),
        ("git", "git log --oneline -5", "low"),
        ("vercel-cli", "vercel --prod", "high"),
        ("docker", "docker rm my-container", "critical"),
        ("github-cli", "gh repo list", "medium"),
    ],
)
def test_seed_headline_query_resolves_correctly(
    seeded_store: ClaimStore,
    tool_id: str,
    command: str,
    expected_risk: str,
) -> None:
    """Every headline demo scenario must produce a non-default-deny
    verdict with the expected risk level. If this test ever fails, the
    demo video would be broken — caller-visible regression in the
    primary demonstrated behaviour."""
    response = QueryEngine(seeded_store).validate_command(
        tool_id=tool_id, command=command
    )
    assert response.match_method in ("exact", "prefix"), (
        f"{command!r} did not match any seeded claim"
    )
    assert response.risk_level is not None
    assert response.risk_level.value == expected_risk


def test_seed_gh_repo_delete_blocks_auto_execute(
    seeded_store: ClaimStore,
) -> None:
    """The single most important demo invariant: Ayiru MUST refuse to
    auto-execute `gh repo delete`, with cited evidence in the verdict."""
    response = QueryEngine(seeded_store).validate_command(
        tool_id="github-cli",
        command="gh repo delete my-org/critical-prod --yes",
    )
    assert response.safe_to_auto_execute is False
    assert response.requires_human_confirmation is True
    assert response.risk_level is not None
    assert response.risk_level.value == "critical"
    # Reasons must explain why auto-execution is blocked.
    joined = " ".join(response.reasons).lower()
    assert "safety policy" in joined or "destructive" in joined
    # Evidence must be cited.
    assert response.evidence
    assert any("github.com" in e.source_uri for e in response.evidence)


def test_seed_unknown_command_returns_default_deny(
    seeded_store: ClaimStore,
) -> None:
    """No-match path still default-denies cleanly on a seeded graph."""
    response = QueryEngine(seeded_store).validate_command(
        tool_id="git", command="git some-unknown-command"
    )
    assert response.match_method == "none"
    assert response.safe_to_auto_execute is False
    assert response.risk_level is None


def test_seed_warns_when_db_already_populated_and_no_reset(tmp_path) -> None:
    """Regression: re-running the seed script without `--reset` silently
    inserts duplicate claims (each one gets a fresh uuid claim_id so the
    matcher's duplicate detector misses them at the SQL layer) AND
    degrades headline-scenario acceptance (the orchestrator marks the
    duplicates as PENDING/L1, hiding them from the matcher). The seed
    script must emit a loud warning in this case so the maintainer
    notices before the demo state is corrupted."""
    db_path = tmp_path / "warn.db"
    database_url = f"sqlite:///{db_path}"

    first = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--database-url", database_url, "--reset",
        ],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert first.returncode == 0

    second = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--database-url", database_url,  # no --reset on the rerun
        ],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert second.returncode == 0
    assert "WARNING" in second.stdout
    assert "--reset" in second.stdout
