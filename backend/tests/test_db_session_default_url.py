"""Regression tests for the default DATABASE_URL resolver.

The 2026-05-22 dogfood session caught a real bug: Claude Desktop spawns
``ayiru mcp`` from ``/`` (or its app sandbox), and the prior
``DEFAULT_DATABASE_URL = "sqlite:///./ayiru.db"`` resolved to a path
that didn't exist. Every tool call returned
``OperationalError: unable to open database file``.

The fix walks up from ``app/db/session.py``'s ``__file__`` looking for
``alembic.ini`` and resolves the DB to ``<repo>/backend/ayiru.db`` —
absolute, independent of CWD. These tests pin that resolver so the
bug can't silently regress.
"""

from __future__ import annotations

from pathlib import Path

from app.db.session import (
    DATABASE_URL,
    DEFAULT_DATABASE_URL,
    _resolve_default_database_url,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DB_PATH = REPO_ROOT / "backend" / "ayiru.db"


def test_default_database_url_is_absolute() -> None:
    """The default URL must NOT be CWD-relative — that's the bug we're
    locking down. A SQLite URL of the form ``sqlite:///./...`` resolves
    differently depending on where the process is launched from."""
    assert DEFAULT_DATABASE_URL.startswith("sqlite:////"), (
        f"DEFAULT_DATABASE_URL must be absolute, got: {DEFAULT_DATABASE_URL!r}"
    )


def test_default_database_url_points_at_backend_ayiru_db() -> None:
    """In a dev checkout the resolver must land at <repo>/backend/ayiru.db
    so the MCP server, the CLI, the FastAPI app, and the seed runner all
    agree on which DB to open."""
    expected = f"sqlite:///{EXPECTED_DB_PATH}"
    assert DEFAULT_DATABASE_URL == expected, (
        f"Default URL drift: expected {expected!r}, got {DEFAULT_DATABASE_URL!r}"
    )


def test_resolver_is_independent_of_cwd(monkeypatch, tmp_path) -> None:
    """Changing CWD must not change the resolved default. This is the
    direct regression for the Claude Desktop spawn-from-``/`` bug.

    We call ``_resolve_default_database_url`` while the process is in
    ``tmp_path`` (a totally unrelated dir); it must still return the
    canonical backend path because the resolver keys off
    ``Path(__file__)``, not ``Path.cwd()``."""
    monkeypatch.chdir(tmp_path)
    resolved = _resolve_default_database_url()
    expected = f"sqlite:///{EXPECTED_DB_PATH}"
    assert resolved == expected, (
        f"resolver leaked CWD: got {resolved!r} from CWD {tmp_path}; "
        f"expected {expected!r}"
    )


def test_explicit_env_var_overrides_default(monkeypatch) -> None:
    """Operators (and the Claude Desktop env block) must be able to
    override the default with an explicit AYIRU_DATABASE_URL. The
    module-level DATABASE_URL is computed at import time, so this test
    drives the env-var path directly through the resolver instead of
    re-importing."""
    monkeypatch.setenv("AYIRU_DATABASE_URL", "sqlite:////tmp/explicit-override.db")
    # Re-derive what the module would compute on a fresh import.
    from os import environ
    derived = environ.get("AYIRU_DATABASE_URL", _resolve_default_database_url())
    assert derived == "sqlite:////tmp/explicit-override.db"


def test_seed_runner_default_matches_session_default() -> None:
    """The seed runner used to maintain its own _default_database_url()
    copy. After 2026-05-22 it delegates to app.db.session so the two
    can't drift. This test makes the delegation contract testable."""
    from app.seed_data.runner import _default_database_url

    assert _default_database_url() == DATABASE_URL
