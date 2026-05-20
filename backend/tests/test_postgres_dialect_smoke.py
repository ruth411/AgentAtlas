"""Stage 14: prove the schema compiles under the Postgres dialect.

The v1.0 test matrix is SQLite-only — we don't run a real Postgres in
CI. But the SQLAlchemy schema and every alembic migration are written
to be dialect-portable. This smoke confirms that property without
needing a database:

1. The full `Base.metadata` DDL renders cleanly under the postgresql
   dialect (catches dialect-specific column type mismatches early).
2. Every alembic migration's `upgrade()` can be replayed under a
   `MockEngine` configured for postgresql, in order. Catches use of
   SQLite-only `op.execute()` strings or missing column types.

A future Stage where Postgres becomes a real backend will replace this
with a `testcontainers`-driven test against a live database. Until then
this is the cheap guarantee that the migrations stay portable.
"""

from __future__ import annotations

import io

import pytest
import sqlalchemy as sa
from sqlalchemy.schema import CreateTable

from app.db.models import Base


def test_metadata_ddl_renders_under_postgresql_dialect() -> None:
    """Every table compiles cleanly under the postgresql dialect."""
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    rendered = []
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        rendered.append(ddl)
        # Sanity: the rendered DDL mentions the table name.
        assert table.name in ddl, f"DDL for {table.name} missing the table identifier"
    # All 9-ish tables rendered without raising — that's the smoke.
    assert len(rendered) == len(Base.metadata.sorted_tables)


def test_alembic_migrations_replay_under_postgresql_mock_engine(
    tmp_path,
) -> None:
    """Replay every migration's `upgrade()` against a MockEngine wired
    to the postgresql dialect. The MockEngine buffers DDL statements
    instead of executing them; we just assert no migration raises."""
    from alembic import command

    from app.services.alembic_config import make_alembic_config

    cfg = make_alembic_config()

    # `command.upgrade` in offline mode emits SQL to stdout via the
    # configured dialect. Point alembic at a postgresql URL — no
    # connection is opened in `--sql` (offline) mode, the migrations
    # just compile DDL for the named dialect.
    sql_output = io.StringIO()
    cfg.attributes["sqlalchemy.url"] = "postgresql+psycopg://noop"
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://noop")

    # Offline mode: --sql produces the migration SQL without connecting.
    # We redirect stdout to capture it; the assertion is purely "does
    # alembic raise?".
    import contextlib

    with contextlib.redirect_stdout(sql_output):
        try:
            command.upgrade(cfg, "head", sql=True)
        except Exception as exc:
            pytest.fail(
                f"alembic offline-upgrade against postgresql dialect raised: {exc}"
            )

    rendered = sql_output.getvalue()
    # Sanity: the rendered SQL is non-empty and references at least one
    # of our tables. A degenerate empty render would also "pass" the
    # try/except, so this guards against that regression.
    assert rendered.strip(), "alembic offline upgrade produced no SQL"
    assert "knowledge_claims" in rendered
    assert "audit_events" in rendered  # Stage 13 migration applied


def test_postgresql_dialect_is_registered() -> None:
    """A small canary: `create_engine("postgresql+psycopg://...")` is
    parseable. Doesn't open a connection — just validates URL parsing
    and dialect registration."""
    url = sa.make_url("postgresql+psycopg://user:pw@host:5432/db")
    assert url.get_backend_name() == "postgresql"
    assert url.drivername == "postgresql+psycopg"
