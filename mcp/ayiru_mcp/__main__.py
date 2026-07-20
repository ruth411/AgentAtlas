"""Entry point: `ayiru-mcp` (console script) and `python -m ayiru_mcp`.

Resolves the wheel-bundled catalog at `ayiru_mcp/data/catalog.db`, points
the shared `app.db.session` at it via the `AYIRU_DATABASE_URL` env var
BEFORE any module that captures the engine at import time runs, then
hands off to the stdio JSON-RPC loop.

The catalog ships inside the wheel as package data. `importlib.resources`
gives us a Traversable — for a normally-installed wheel this is already
on the local filesystem, so `str(...)` returns a usable path. For
zip-imported deployments (rare for pip-installed wheels, but possible),
we copy the resource to a temp file and point the engine at that.
"""

from __future__ import annotations

import os
import sys
import tempfile
from importlib import resources
from pathlib import Path


_CATALOG_RESOURCE = "catalog.db"


def _resolve_bundled_catalog_path() -> Path:
    try:
        traversable = resources.files("ayiru_mcp.data").joinpath(_CATALOG_RESOURCE)
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        raise RuntimeError(
            "ayiru-mcp was installed without the bundled catalog at "
            "`ayiru_mcp/data/catalog.db`. Reinstall the wheel."
        ) from exc

    if not traversable.is_file():
        raise RuntimeError(
            "ayiru-mcp's bundled catalog is missing. The wheel was built "
            "without the data file, or it was deleted from site-packages."
        )

    # Try the simple path. For regular (non-zip) wheel installs the resource
    # is already on disk and str() returns the path.
    try:
        candidate = Path(str(traversable))
        if candidate.is_file():
            return candidate
    except (TypeError, ValueError):
        pass

    # Fallback: extract to a temp file. The directory lives for the
    # lifetime of the process; sqlite holds the file open, so any cleanup
    # has to wait until we exit. We accept the trade-off (one temp file
    # per process) because this branch is the unusual zip-import case.
    tmp_dir = tempfile.mkdtemp(prefix="ayiru-mcp-")
    extracted = Path(tmp_dir) / _CATALOG_RESOURCE
    extracted.write_bytes(traversable.read_bytes())
    return extracted


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _configure_database_url(catalog: Path) -> str:
    bundled_url = f"sqlite:///{catalog}"
    if _truthy_env("AYIRU_MCP_ALLOW_EXTERNAL_DB"):
        configured = os.environ.get("AYIRU_DATABASE_URL", "").strip()
        if configured:
            return configured
    os.environ["AYIRU_DATABASE_URL"] = bundled_url
    return bundled_url


def main() -> None:
    catalog = _resolve_bundled_catalog_path()
    # Point ayiru-core's session.py at the bundled DB BEFORE we import
    # anything that calls `create_database_engine()` at import time.
    # Ignore ambient AYIRU_DATABASE_URL by default so `ayiru-mcp` stays
    # install-and-go; only an explicit opt-in may override the bundled DB.
    _configure_database_url(catalog)

    # Lazy import after the env var is set so the engine resolver sees it.
    from ayiru_mcp._internal.server import build_default_server

    # `build_default_server()` constructs a `ClaimStore` using the env-var
    # URL, then wires the protocol loop. No FastAPI is imported anywhere
    # in this path — the only deps that resolve are pydantic, sqlalchemy,
    # and the ayiru-core schemas. `fastembed` stays unimported unless the
    # caller explicitly invoked `ask()` in semantic mode AND the optional
    # `[semantic]` extra is installed.
    build_default_server(
        allow_hidden_tools=False,
    ).serve(stdin=sys.stdin, stdout=sys.stdout)


if __name__ == "__main__":
    main()
