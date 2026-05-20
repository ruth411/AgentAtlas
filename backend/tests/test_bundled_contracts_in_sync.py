"""Stage 14: ensure the wheel-bundled contracts match the canonical files.

The repo-root `contracts/*.v1.json` files are the source of truth.
`backend/app/contracts/*.json` is a build-time copy that ships inside
the wheel. Both must be byte-identical — if a contract is edited at the
repo root but the package copy isn't synced, a `pip install` user gets
a stale ruleset.

This test is the lockstep enforcer. If it fails: rerun the contract
sync (currently a manual `cp contracts/*.json backend/app/contracts/`)
before publishing the wheel.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONTRACTS = REPO_ROOT / "contracts"
BUNDLED_CONTRACTS = REPO_ROOT / "backend" / "app" / "contracts"


def test_every_source_contract_has_a_bundled_copy() -> None:
    source = {path.name for path in SOURCE_CONTRACTS.glob("*.json")}
    bundled = {path.name for path in BUNDLED_CONTRACTS.glob("*.json")}
    missing = source - bundled
    extra = bundled - source
    assert not missing, (
        f"Contracts present at repo root but missing from the bundled wheel "
        f"copy: {sorted(missing)}. Run `cp contracts/*.json backend/app/contracts/` "
        "and commit before shipping."
    )
    assert not extra, (
        f"Contracts present in the bundled wheel copy but missing from the "
        f"repo root: {sorted(extra)}. The repo root is the source of truth; "
        "the bundled copy must not invent new files."
    )


def test_every_bundled_contract_is_byte_identical_to_source() -> None:
    for source_path in SOURCE_CONTRACTS.glob("*.json"):
        bundled_path = BUNDLED_CONTRACTS / source_path.name
        assert bundled_path.is_file(), (
            f"Bundled copy missing for {source_path.name}; see the lockstep test above."
        )
        # Compare bytes, not parsed JSON — formatting changes (whitespace,
        # key order) would silently desynchronise the hashes consumers
        # depend on for caching and replay.
        source_bytes = source_path.read_bytes()
        bundled_bytes = bundled_path.read_bytes()
        assert source_bytes == bundled_bytes, (
            f"Bundled {source_path.name} differs from the canonical copy at the "
            f"repo root. Rerun the sync: "
            f"`cp contracts/{source_path.name} backend/app/contracts/`."
        )
