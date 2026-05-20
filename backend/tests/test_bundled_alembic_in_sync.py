"""Stage 14: bundled alembic migrations must match the canonical copies.

Same lockstep contract as `test_bundled_contracts_in_sync.py`. The
canonical migrations live at `backend/alembic/`; a build-time copy
ships at `backend/app/_alembic/` and is loaded by
`make_alembic_config()` when the source-tree `alembic.ini` isn't
visible (i.e. a wheel install).

If you edit a migration, mirror the file into the bundled directory:

    cp backend/alembic/versions/<file>.py backend/app/_alembic/versions/

The test below is the lockstep enforcer.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "backend" / "alembic"
BUNDLED_DIR = REPO_ROOT / "backend" / "app" / "_alembic"


def _python_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def test_bundled_alembic_tree_mirrors_canonical() -> None:
    source = _python_files(SOURCE_DIR)
    bundled = _python_files(BUNDLED_DIR)
    missing = source - bundled
    extra = bundled - source
    assert not missing, (
        f"Migrations present at backend/alembic/ but missing from the "
        f"bundled wheel copy: {sorted(missing)}. Mirror them into "
        "`backend/app/_alembic/` before shipping."
    )
    assert not extra, (
        f"Migrations present in the bundled wheel copy but missing from "
        f"backend/alembic/: {sorted(extra)}. The canonical tree is the "
        "source of truth; remove the extras from the bundle."
    )


def test_every_bundled_migration_is_byte_identical_to_source() -> None:
    for source_path in SOURCE_DIR.rglob("*.py"):
        if "__pycache__" in source_path.parts:
            continue
        relative = source_path.relative_to(SOURCE_DIR)
        bundled_path = BUNDLED_DIR / relative
        assert bundled_path.is_file(), (
            f"Bundled copy missing for {relative}; see the lockstep test above."
        )
        source_bytes = source_path.read_bytes()
        bundled_bytes = bundled_path.read_bytes()
        assert source_bytes == bundled_bytes, (
            f"Bundled {relative} differs from the canonical copy at "
            f"backend/alembic/. Rerun the mirror: "
            f"`cp backend/alembic/{relative} backend/app/_alembic/{relative}`."
        )
