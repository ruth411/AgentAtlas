"""Smoke test: the bundled gh catalog answers a real question.

The slim catalog lives at ``mcp/ayiru_mcp/data/catalog.db`` and is rebuilt
by ``tools/scripts/build_slim_catalog.py``. This test asserts the file
exists, points `ClaimStore` at it, and runs `QueryEngine.ask()` end to
end. The expected hit is the well-known gh-authenticate-from-CI claim
that has a citation back to ``cli.github.com``.

If this test fails after a catalog refresh, either:

1. The source DB lost the gh-auth claim (regression in the seed pipeline).
2. The slim builder filtered out the wrong tool family (regression in
   ``build_slim_catalog.py``).
3. The query engine started returning a different top hit (lexical or
   semantic re-ranker regression in ``ayiru-core``).

Run the builder directly to debug:

    python tools/scripts/build_slim_catalog.py \\
        --source backend/ayiru_v0.2_bulk.db \\
        --output mcp/ayiru_mcp/data/catalog.db \\
        --tool-families gh
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from app.services.claim_store import ClaimStore
from app.services.query_engine import QueryEngine


def _bundled_catalog_path() -> Path:
    """Resolve the bundled catalog the same way `ayiru_mcp.__main__` does."""
    traversable = resources.files("ayiru_mcp.data").joinpath("catalog.db")
    return Path(str(traversable))


def test_bundled_catalog_exists_and_is_nontrivial() -> None:
    path = _bundled_catalog_path()
    assert path.is_file(), (
        f"Bundled catalog missing at {path}. Rebuild with "
        f"`python tools/scripts/build_slim_catalog.py --source <bulk.db> "
        f"--output mcp/ayiru_mcp/data/catalog.db`."
    )
    # ~1.5 MB for the gh-only catalog; assert we're at least somewhere
    # north of "empty schema" so an accidentally truncated copy fails
    # loudly.
    assert path.stat().st_size > 500_000, (
        f"Bundled catalog is suspiciously small ({path.stat().st_size} bytes). "
        f"The schema-only DB is ~150 KB; the populated gh catalog should be > 1 MB."
    )


def test_ask_against_bundled_catalog_returns_gh_auth_claim() -> None:
    path = _bundled_catalog_path()
    if not path.is_file():
        pytest.skip("Bundled catalog not built yet; see build_slim_catalog.py")

    store = ClaimStore(database_url=f"sqlite:///{path}")
    response = QueryEngine(store).ask(
        question="how do I authenticate gh from a CI workflow?",
        limit=5,
    )

    assert response.answers, (
        "ask() returned no answers — either the bundled catalog is empty or "
        "the matcher's score threshold rejected every gh claim."
    )

    # Top answer should be a gh-* surface from cli.github.com.
    top = response.answers[0]
    assert top.tool_id.startswith("gh"), (
        f"top answer is from tool_id {top.tool_id!r}; "
        f"a question about gh should match a gh-* surface"
    )
    cli_github_hits = [
        ev for ev in top.evidence if "cli.github.com" in ev.source_uri
    ]
    assert cli_github_hits, (
        f"top answer's evidence URIs were {[ev.source_uri for ev in top.evidence]!r}; "
        f"expected at least one cli.github.com citation"
    )
