"""Stage 14: bundled seed artifacts must match the canonical copies.

Same lockstep contract as `test_bundled_contracts_in_sync.py`: the repo
root's `data/seed_artifacts/` directory is the source of truth and the
wheel-bundled copies under `backend/app/seed_data/artifacts/` must stay
byte-identical with it. Edits to the canonical files need to be mirrored
into the bundle before a release wheel is built.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "data" / "seed_artifacts"
BUNDLED_DIR = REPO_ROOT / "backend" / "app" / "seed_data" / "artifacts"


def _relative_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def test_seed_artifact_tree_is_mirrored() -> None:
    source = _relative_files(SOURCE_DIR)
    bundled = _relative_files(BUNDLED_DIR)
    missing = source - bundled
    extra = bundled - source
    assert not missing, (
        "Seed artifacts present at the repo root but missing from the "
        f"bundled wheel copy: {sorted(missing)}. Mirror them into "
        "`backend/app/seed_data/artifacts/` before shipping."
    )
    assert not extra, (
        "Seed artifacts present in the bundled wheel copy but missing "
        f"from the repo root: {sorted(extra)}. The repo root is the "
        "source of truth; remove the extras."
    )


def test_every_bundled_artifact_is_byte_identical_to_source() -> None:
    for source_path in SOURCE_DIR.rglob("*"):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(SOURCE_DIR)
        bundled_path = BUNDLED_DIR / relative
        assert bundled_path.is_file(), (
            f"Bundled copy missing for {relative}; see the lockstep test above."
        )
        source_bytes = source_path.read_bytes()
        bundled_bytes = bundled_path.read_bytes()
        assert source_bytes == bundled_bytes, (
            f"Bundled {relative} differs from the canonical copy. Rerun "
            f"`cp data/seed_artifacts/{relative} backend/app/seed_data/artifacts/{relative}`."
        )
