"""Shared test fixtures.

Preferred strategy: stand up a real uvicorn server on a free localhost
port for the whole session and point both SDK clients at it.

Fallback strategy: some sandboxes forbid binding `127.0.0.1`. In that
case we keep the same app + DB + SDK code under test, but route requests
through in-process transports instead of a real socket. That preserves
meaningful integration coverage in restricted environments instead of
skipping the whole client suite.

The DB is session-scoped (one fresh SQLite file for the entire run);
tests use uniquely-suffixed claim IDs so they don't collide on inserts.
Tests that need a clean DB per run can use the function-scoped
`isolated_claim_store` fixture, which clears tables before yielding."""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Iterator
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


def _can_bind_localhost() -> bool:
    try:
        _free_port()
    except OSError:
        return False
    return True


@pytest.fixture(scope="session")
def _session_db() -> Iterator[str]:
    fd, path = tempfile.mkstemp(prefix="ayiru-sdk-tests-", suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    os.environ["AYIRU_DATABASE_URL"] = url
    # The backend's app.db.session module reads AYIRU_DATABASE_URL at
    # import time; we set it BEFORE first import here, but if anything
    # imported it earlier (e.g. a plugin), the module-level bindings
    # are stale. The lazy `rebind_default_engine` helper (Stage P1-3)
    # is the canonical way to force a refresh.
    try:
        yield url
    finally:
        os.environ.pop("AYIRU_DATABASE_URL", None)
        Path(path).unlink(missing_ok=True)


@pytest.fixture(scope="session")
def _backend_app(_session_db):
    """Import and initialize the real backend app against the temp DB."""

    from app.db.session import init_db, rebind_default_engine  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    rebind_default_engine()  # honor the temp DB even if the module was preloaded
    init_db()
    return app


@pytest.fixture(scope="session")
def _server(_backend_app) -> Iterator[str | None]:
    """Start uvicorn when localhost binds are available.

    Defends against three failure modes a test author shouldn't have
    to think about:
      1. Module already imported by a plugin before our env-var was set
         → we call `rebind_default_engine()` to re-bind against the
         current env. Without this, the server would write to
         backend/ayiru.db (the dev DB) instead of our temp file.
      2. uvicorn fails to start within 5s → raise loudly instead of
         leaving tests timing out one by one.
      3. Test process killed mid-run → the `try/finally` issues a
         graceful shutdown via `should_exit`, plus a hard
         `server.shutdown()` synchronously so the OS releases the
         port even if the thread is jammed.

    If the environment forbids binding a local socket, yield `None` and
    let the per-client fixtures fall back to in-process transports.
    """
    if not _can_bind_localhost():
        yield None
        return

    import uvicorn  # noqa: PLC0415

    port = _free_port()
    config = uvicorn.Config(_backend_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Block until the server reports it's ready; uvicorn flips `started`
    # once the socket is bound and accepting.
    deadline = time.monotonic() + 5.0
    while not getattr(server, "started", False):
        if time.monotonic() > deadline:
            server.should_exit = True
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
        # Two-phase shutdown: the cooperative flag lets uvicorn drain
        # in-flight requests, but if it's stuck we don't want to hold
        # the port forever — join with a short timeout, then move on.
        server.should_exit = True
        thread.join(timeout=5.0)


@pytest.fixture
def base_url(_server) -> str:
    return _server or "http://testserver"


@pytest.fixture
def sync_client_factory(_backend_app, _server):
    """Yield a contextmanager that builds an `Ayiru` client.

    Prefers the real uvicorn socket when available. Otherwise uses
    Starlette's sync test transport against the same app instance.
    """

    from ayiru_client import Ayiru  # noqa: PLC0415

    @contextlib.contextmanager
    def factory(*, api_key: str | None = None):
        if _server is not None:
            with Ayiru(base_url=_server, api_key=api_key) as client:
                yield client
            return

        from starlette.testclient import TestClient  # noqa: PLC0415

        with TestClient(_backend_app) as test_client:
            client = Ayiru(
                base_url=str(test_client.base_url),
                api_key=api_key,
                transport=test_client._transport,
            )
            try:
                yield client
            finally:
                client.close()

    return factory


@pytest.fixture
def async_client_factory(_backend_app, _server):
    """Yield an async contextmanager that builds an `AsyncAyiru` client."""

    from ayiru_client import AsyncAyiru  # noqa: PLC0415

    @contextlib.asynccontextmanager
    async def factory(*, api_key: str | None = None) -> AsyncIterator[AsyncAyiru]:
        if _server is not None:
            async with AsyncAyiru(base_url=_server, api_key=api_key) as client:
                yield client
            return

        transport = httpx.ASGITransport(app=_backend_app, client=("testclient", 50000))
        async with AsyncAyiru(
            base_url="http://testserver",
            api_key=api_key,
            transport=transport,
        ) as client:
            yield client

    return factory


@pytest.fixture
def claim_store(_backend_app):
    """Live ClaimStore handle — same DB the server uses, since both
    read `AYIRU_DATABASE_URL`."""

    from app.services.claim_store import get_claim_store  # noqa: PLC0415

    return get_claim_store()


@pytest.fixture
def isolated_claim_store(_backend_app):
    """Function-scoped variant of `claim_store` that truncates the
    knowledge-graph tables before yielding. Use when a test asserts on
    "the graph is empty" or "the graph holds exactly N claims" — the
    default session-scoped DB would carry over state from earlier tests
    and cause non-deterministic flakes."""

    from app.db.models import (  # noqa: PLC0415
        AuditEventRecord,
        EvidenceRecord,
        IngestionRunRecord,
        KnowledgeClaimRecord,
        VerificationResultRecord,
    )
    from app.db.session import engine  # noqa: PLC0415
    from app.services.claim_store import get_claim_store  # noqa: PLC0415
    from sqlalchemy import delete  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    with Session(engine) as session:
        # FK order matters — evidence and verifications dangle off claims.
        session.execute(delete(VerificationResultRecord))
        session.execute(delete(EvidenceRecord))
        session.execute(delete(AuditEventRecord))
        session.execute(delete(IngestionRunRecord))
        session.execute(delete(KnowledgeClaimRecord))
        session.commit()
    return get_claim_store()
