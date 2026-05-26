from collections.abc import Generator
from os import environ
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


def _resolve_default_database_url() -> str:
    """Return the SQLite URL Ayiru should use when no AYIRU_DATABASE_URL
    env var is set.

    Caller's CWD is unreliable — Claude Desktop, IDE-spawned MCP servers,
    cron jobs, and Docker entrypoints all run from working directories
    where ``./ayiru.db`` resolves to nonsense. The MCP server bug caught
    in the 2026-05-22 dogfood session was exactly this: Claude Desktop
    spawned ``ayiru mcp`` from ``/`` and every tool call returned
    ``OperationalError: unable to open database file``.

    Resolution order (first hit wins):
      1. Walk up from this module's ``__file__`` looking for
         ``alembic.ini`` — that marks a dev checkout where the canonical
         DB lives at ``<repo>/backend/ayiru.db``.
      2. Fall back to CWD-relative ``./ayiru.db`` (preserves the v0.1
         behavior for wheel-installed runs where the user opts in by
         setting AYIRU_DATABASE_URL explicitly or by `cd`-ing first).

    Tests don't rely on this path — they pass an explicit
    ``database_url=`` to ``ClaimStore``, bypassing the module-level
    default entirely.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "alembic.ini").is_file():
            return f"sqlite:///{parent / 'ayiru.db'}"
        # Stop walking at the repo root marker so we don't accidentally
        # match an ``alembic.ini`` far up the filesystem.
        if (parent / ".git").exists():
            break
    return "sqlite:///./ayiru.db"


DEFAULT_DATABASE_URL = _resolve_default_database_url()


def current_database_url() -> str:
    """Return the database URL the runtime should currently use.

    Reads ``AYIRU_DATABASE_URL`` from the environment **on every call**
    so callers that set the env var after this module is first imported
    (a common pattern in tests and in Python processes that load
    settings from a `.env` file post-import) see their override take
    effect. The module-level ``DATABASE_URL`` constant is a snapshot of
    the value at import time and may be stale; prefer this function in
    new code."""

    return environ.get("AYIRU_DATABASE_URL", DEFAULT_DATABASE_URL)


# Snapshot of the URL at import time. Kept for back-compat with callers
# that imported the constant; new code should prefer current_database_url().
DATABASE_URL = current_database_url()


def create_database_engine(database_url: str | None = None) -> Engine:
    """Build a SQLAlchemy engine for ``database_url``, or for the
    current env-derived URL when omitted. Lazy reads of the env let
    tests rebind the engine after setting ``AYIRU_DATABASE_URL``
    without having to also reload the module."""

    url = database_url or current_database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def rebind_default_engine(database_url: str | None = None) -> None:
    """Recreate the module-level ``engine`` and ``SessionLocal`` against
    a new database URL (or against the current ``AYIRU_DATABASE_URL``
    when ``database_url`` is omitted).

    Production code never needs this — it's the escape hatch for tests
    and embedded processes that mutate the database URL env var after
    importing ``app.db.session``. Also clears the cached default
    ``ClaimStore`` so the next ``get_claim_store()`` rebuilds against
    the new factory."""

    global engine, SessionLocal, DATABASE_URL
    new_url = database_url or current_database_url()
    try:
        engine.dispose()
    except Exception:  # noqa: BLE001
        # Dispose can fail if the prior engine was already closed —
        # we just need the new one to take over, so swallow.
        pass
    engine = create_database_engine(new_url)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    DATABASE_URL = new_url
    # Clear the cached default store. Imported locally to avoid a
    # module-level circular import at file load.
    from app.services import claim_store as _cs

    _cs._CLAIM_STORE = None


def init_db(target_engine: Engine | None = None) -> None:
    """Create all tables on ``target_engine`` (or the current module
    default if omitted). The default is resolved at call time, not at
    function definition time, so it tracks any ``rebind_default_engine``
    invocations."""

    Base.metadata.create_all(bind=target_engine or engine)


def get_db_session() -> Generator[Session]:
    with SessionLocal() as session:
        yield session
