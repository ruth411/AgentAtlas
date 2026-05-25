"""Shared test fixtures.

Strategy: stand up a real uvicorn server on a free localhost port for
the whole session, point both the sync and async SDK clients at it.
Tests then exercise the SDK over an actual HTTP socket — which is the
closest possible parallel to how the package will be used in
production, and which works uniformly across `httpx.Client` and
`httpx.AsyncClient` (httpx's ASGITransport is async-only and crashes
on `Client.close()`).

The DB is session-scoped (one fresh SQLite file for the entire run);
tests use uniquely-suffixed claim IDs so they don't collide on inserts."""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

# Reach the backend source tree without installing it.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_SRC = _REPO_ROOT / "backend"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))


def _free_port() -> int:
    """Bind, read port, release. Brief race window — fine for tests."""

    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def _session_db() -> Iterator[str]:
    fd, path = tempfile.mkstemp(prefix="ayiru-sdk-tests-", suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    os.environ["AYIRU_DATABASE_URL"] = url
    try:
        yield url
    finally:
        os.environ.pop("AYIRU_DATABASE_URL", None)
        Path(path).unlink(missing_ok=True)


@pytest.fixture(scope="session")
def _server(_session_db) -> Iterator[str]:
    """Start uvicorn in a background thread; yield the base_url."""

    import uvicorn  # noqa: PLC0415
    from app.db.session import init_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    init_db()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Block until the server reports it's ready; uvicorn flips `started`
    # once the socket is bound and accepting.
    deadline = time.monotonic() + 5.0
    while not getattr(server, "started", False):
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start within 5s")
        time.sleep(0.05)

    base_url = f"http://127.0.0.1:{port}"
    try:
        # Final readiness probe. Health is mounted without the /v1/
        # prefix (it's universal, not versioned) in app.main.
        with httpx.Client() as h:
            h.get(f"{base_url}/health", timeout=5.0).raise_for_status()
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


@pytest.fixture
def base_url(_server) -> str:
    return _server


@pytest.fixture
def claim_store(_server):
    """Live ClaimStore handle — same DB the server uses, since both
    read `AYIRU_DATABASE_URL`."""

    from app.services.claim_store import get_claim_store  # noqa: PLC0415

    return get_claim_store()
