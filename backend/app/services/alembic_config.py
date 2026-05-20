"""Stage 14: unified alembic-config resolver.

`ayiru migrate`, `ayiru seed --reset`, and the auto-migration
hook on `ayiru serve` all need an `alembic.Config` pointing at a
real migration script directory. Three install paths exist:

1. **Source-tree checkout** — `backend/alembic.ini` + `backend/alembic/`.
2. **Wheel install** — neither exists at the wheel's runtime path; the
   bundled copy at `app/_alembic/` is what ships.
3. **Custom config** — operators with their own alembic layout point
   `AYIRU_ALEMBIC_INI` at their ini.

`make_alembic_config(database_url)` returns a fully-configured `Config`
that works in all three modes. Callers never need to know which path
produced it.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config


def _bundled_script_location() -> Path | None:
    """Return the bundled migration directory path, or `None` if the
    package data wasn't installed (degenerate / hand-edited install)."""
    try:
        bundled = resources.files("app").joinpath("_alembic")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(bundled))
    return path if path.is_dir() else None


def _source_tree_ini() -> Path | None:
    """Walk upward from this file looking for `alembic.ini`."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "alembic.ini"
        if candidate.is_file():
            return candidate
        if (parent / ".git").exists():
            break
    return None


def make_alembic_config(database_url: str | None = None) -> "Config":
    """Return an alembic `Config` ready for `command.upgrade(cfg, ...)`.

    Lookup order:

    1. `AYIRU_ALEMBIC_INI` env var (operator override).
    2. Source-tree `backend/alembic.ini` (developer checkout).
    3. Programmatic config pointing at the bundled `app/_alembic/`
       directory (wheel install).

    A `RuntimeError` surfaces only when option 3 fails — i.e. the
    package data is missing, which means the wheel is corrupt.
    """
    from alembic.config import Config

    override = os.environ.get("AYIRU_ALEMBIC_INI", "").strip()
    if override:
        ini_path = Path(override)
        if not ini_path.is_file():
            raise RuntimeError(
                f"AYIRU_ALEMBIC_INI points to '{ini_path}', which does not exist."
            )
        cfg = Config(str(ini_path))
        # `script_location` is interpreted relative to the ini's
        # directory. Switch the CWD so a `script_location = alembic`
        # entry resolves correctly even if the caller is elsewhere.
        os.chdir(ini_path.parent)
        if database_url:
            cfg.set_main_option("sqlalchemy.url", database_url)
        return cfg

    source_ini = _source_tree_ini()
    if source_ini is not None:
        cfg = Config(str(source_ini))
        os.chdir(source_ini.parent)
        if database_url:
            cfg.set_main_option("sqlalchemy.url", database_url)
        return cfg

    # Wheel install: build the config programmatically from the bundled
    # migration directory.
    bundled = _bundled_script_location()
    if bundled is None:
        raise RuntimeError(
            "Bundled alembic migrations were not found at `app/_alembic/`. "
            "The wheel may be corrupt; reinstall, or run from a checkout."
        )
    cfg = Config()
    cfg.set_main_option("script_location", str(bundled))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg
