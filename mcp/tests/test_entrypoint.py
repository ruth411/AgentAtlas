from __future__ import annotations

import os
from pathlib import Path

from ayiru_mcp import __main__ as entrypoint


def test_configure_database_url_ignores_ambient_override_by_default(
    monkeypatch,
) -> None:
    catalog = Path("/tmp/bundled-catalog.db")
    monkeypatch.setenv("AYIRU_DATABASE_URL", "sqlite:////tmp/ambient.db")
    monkeypatch.delenv("AYIRU_MCP_ALLOW_EXTERNAL_DB", raising=False)

    configured = entrypoint._configure_database_url(catalog)

    assert configured == "sqlite:////tmp/bundled-catalog.db"
    assert os.environ["AYIRU_DATABASE_URL"] == "sqlite:////tmp/bundled-catalog.db"


def test_configure_database_url_allows_explicit_external_override(
    monkeypatch,
) -> None:
    catalog = Path("/tmp/bundled-catalog.db")
    monkeypatch.setenv("AYIRU_DATABASE_URL", "sqlite:////tmp/external.db")
    monkeypatch.setenv("AYIRU_MCP_ALLOW_EXTERNAL_DB", "1")

    configured = entrypoint._configure_database_url(catalog)

    assert configured == "sqlite:////tmp/external.db"
    assert os.environ["AYIRU_DATABASE_URL"] == "sqlite:////tmp/external.db"
