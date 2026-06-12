"""Smoke test: the bundled gh catalog returns typed structured records.

The slim catalog lives at ``mcp/ayiru_mcp/data/catalog.db`` and is rebuilt
by ``tools/scripts/build_slim_catalog.py --structured-only``. v0.2 ships
**only** the structured tables (`subjects`, `capabilities`, `constraints`,
`effects`) — no prose claims. This test asserts:

1. The bundled catalog exists.
2. `get_capabilities` returns typed records with `source="structured"`,
   `detail` is a dict (not a string), and `verification_level` is
   `L3_runtime_verified`.
3. `get_effects` returns typed booleans (destructive / mutates_remote_state).

If this test fails after a catalog refresh, either:

1. The bulk DB lost the structured gh rows (regression in
   ``tools/scripts/structured_ingest_gh.py``).
2. The slim builder filtered out the wrong family (regression in
   ``build_slim_catalog.py``).
3. The query engine's structured-first read path regressed.

Run the builder directly to debug:

    python tools/scripts/build_slim_catalog.py \\
        --source backend/ayiru_v0.2_bulk.db \\
        --output mcp/ayiru_mcp/data/catalog.db \\
        --tool-families gh \\
        --structured-only
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
        f"--output mcp/ayiru_mcp/data/catalog.db --tool-families gh "
        f"--structured-only`."
    )
    # ~1.4 MB for the gh-only structured catalog. Assert we're above the
    # schema-only floor so an accidentally truncated copy fails loudly.
    assert path.stat().st_size > 500_000, (
        f"Bundled catalog is suspiciously small ({path.stat().st_size} bytes). "
        f"The schema-only DB is ~150 KB; the populated gh structured "
        f"catalog should be > 1 MB."
    )


def test_get_capabilities_against_bundled_catalog_returns_structured_records() -> None:
    """The v0.2 thesis pin: an MCP client calling `get_capabilities` for a
    bundled subject must get back typed structured records (NOT prose),
    with `source=structured` and `detail` as a dict carrying real
    `argv_schema` / `flag_schema` fields."""

    path = _bundled_catalog_path()
    if not path.is_file():
        pytest.skip("Bundled catalog not built yet; see build_slim_catalog.py")

    store = ClaimStore(database_url=f"sqlite:///{path}")
    response = QueryEngine(store).get_capabilities(
        subject_id="gh-pr-create",
        capability_types=["invocation"],
        limit=1,
    )

    assert response.capabilities, (
        "get_capabilities returned no records — bundled catalog may be "
        "missing the structured gh-pr-create rows."
    )

    top = response.capabilities[0]
    assert top.source == "structured", (
        f"top capability source={top.source!r}; expected 'structured'. "
        f"The bundled catalog ships only structured rows in v0.2; a "
        f"'projected' source means the structured-first read path regressed."
    )
    assert top.capability_type == "invocation"
    assert top.verification_level == "L3_runtime_verified", (
        f"verification_level={top.verification_level!r}; expected "
        f"L3_runtime_verified because the parser ran the binary."
    )
    assert isinstance(top.detail, dict), (
        f"detail type={type(top.detail).__name__}; expected dict. "
        f"Prose statements signal the structured-pivot regressed."
    )
    assert top.detail.get("command") == "gh pr create"
    assert "flag_schema" in top.detail
    assert isinstance(top.detail["flag_schema"], list)
    assert len(top.detail["flag_schema"]) > 5, (
        f"flag_schema has {len(top.detail['flag_schema'])} entries; "
        f"`gh pr create` should have >5 typed flags."
    )
    sample_flag = top.detail["flag_schema"][0]
    for required_field in ("name", "value_type", "takes_value", "required", "description"):
        assert required_field in sample_flag, (
            f"flag missing typed field {required_field!r}: {sample_flag!r}"
        )


def test_get_effects_against_bundled_catalog_returns_typed_records() -> None:
    """`gh repo delete` ships as a destructive, irreversible, remote-mutating
    effect. The bundled catalog must return those as typed structured
    records, not prose statements."""

    path = _bundled_catalog_path()
    if not path.is_file():
        pytest.skip("Bundled catalog not built yet; see build_slim_catalog.py")

    store = ClaimStore(database_url=f"sqlite:///{path}")
    response = QueryEngine(store).get_effects(subject_id="gh-repo-delete")

    assert response.effects, (
        "get_effects returned no rows — `gh repo delete` should ship with "
        "at least one typed effect record."
    )

    # Every effect record's detail is a structured dict for v0.2 bundled gh.
    for effect in response.effects:
        assert effect.source == "structured"
        assert isinstance(effect.detail, dict)
