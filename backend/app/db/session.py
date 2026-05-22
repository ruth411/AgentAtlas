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
DATABASE_URL = environ.get("AYIRU_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_database_engine(database_url: str = DEFAULT_DATABASE_URL) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(target_engine: Engine = engine) -> None:
    Base.metadata.create_all(bind=target_engine)


def get_db_session() -> Generator[Session]:
    with SessionLocal() as session:
        yield session
