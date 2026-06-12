"""Lockstep: every wheel-bundled contract is byte-identical to the canonical
copy at the repo root.

The repo-root ``contracts/*.v1.json`` files are the source of truth.
Bundled copies ship in two wheels:

- Lean trust contracts (``risk_model``, ``confidence_model``, ``query_policy``,
  ``tool_trust_sources``, ``ayiru_stage_0.v{1,2}``) ship in ``ayiru-core``
  under ``core/app/contracts/``.
- Ingestion + runtime-verification contracts (``*_ingestion_sources``,
  ``runtime_verification_sources``) ship in ``ayiru`` under
  ``backend/app/contracts/``.

If a contract is edited at the repo root but its bundled copy is not
re-synced, a ``pip install`` user gets a stale ruleset. This test is the
lockstep enforcer.

Fix on failure:

    cp contracts/<name>.json core/app/contracts/<name>.json     # lean
    cp contracts/<name>.json backend/app/contracts/<name>.json  # ingestion

Or run whatever scripted sync helper supersedes the manual ``cp`` once
it lands.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONTRACTS = REPO_ROOT / "contracts"
_CORE_BUNDLED = REPO_ROOT / "core" / "app" / "contracts"
_BACKEND_BUNDLED = REPO_ROOT / "backend" / "app" / "contracts"


def _bundled_path_for(name: str) -> Path | None:
    for root in (_CORE_BUNDLED, _BACKEND_BUNDLED):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _union_bundled_names() -> set[str]:
    return {
        path.name
        for root in (_CORE_BUNDLED, _BACKEND_BUNDLED)
        for path in root.glob("*.json")
    }


def test_every_source_contract_has_a_bundled_copy() -> None:
    source = {path.name for path in SOURCE_CONTRACTS.glob("*.json")}
    bundled = _union_bundled_names()
    missing = source - bundled
    extra = bundled - source
    assert not missing, (
        f"Contracts present at repo root but missing from every bundled wheel "
        f"copy: {sorted(missing)}. Sync the file into either core/app/contracts/ "
        f"(lean contracts: risk/confidence/query_policy/tool_trust/ayiru_stage_0) "
        f"or backend/app/contracts/ (ingestion + runtime-verification sources) "
        f"and commit before shipping."
    )
    assert not extra, (
        f"Contracts present in a bundled wheel copy but missing from the "
        f"repo root: {sorted(extra)}. The repo root is the source of truth; "
        "the bundled copies must not invent new files."
    )


def test_no_contract_is_duplicated_across_both_bundled_locations() -> None:
    """A contract belongs to exactly one wheel — lean or ingestion. If a
    file shows up in both bundled directories, an editor will eventually
    edit one and not the other, silently desynchronising consumers."""
    core_names = {path.name for path in _CORE_BUNDLED.glob("*.json")}
    backend_names = {path.name for path in _BACKEND_BUNDLED.glob("*.json")}
    duplicates = core_names & backend_names
    assert not duplicates, (
        f"These contracts are present in BOTH core/app/contracts/ and "
        f"backend/app/contracts/: {sorted(duplicates)}. Remove the copy from "
        f"whichever wheel doesn't own it (lean → core, ingestion → backend)."
    )


def test_every_bundled_contract_is_byte_identical_to_source() -> None:
    for source_path in SOURCE_CONTRACTS.glob("*.json"):
        bundled_path = _bundled_path_for(source_path.name)
        assert bundled_path is not None, (
            f"Bundled copy missing for {source_path.name}; see the lockstep "
            f"test above for where it should live."
        )
        # Compare bytes, not parsed JSON — formatting changes (whitespace,
        # key order) would silently desynchronise the hashes consumers
        # depend on for caching and replay.
        source_bytes = source_path.read_bytes()
        bundled_bytes = bundled_path.read_bytes()
        assert source_bytes == bundled_bytes, (
            f"Bundled {source_path.name} (at {bundled_path}) differs from the "
            f"canonical copy at the repo root. Rerun the sync: "
            f"`cp contracts/{source_path.name} {bundled_path.parent.relative_to(REPO_ROOT)}/`."
        )
