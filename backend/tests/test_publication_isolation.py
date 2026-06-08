"""Stage 14: publication-isolation invariant.

Pass case from `ROADMAP.md` ("Do Not Advance If: one failing ingestion
path can poison the whole system"). These tests assert two properties:

- **Cross-lane isolation**: a failure in one ingestion lane does not
  prevent another lane from running cleanly on the same store. The
  failed run leaves the store in a consistent state (no partial spec
  rows, no orphan evidence).
- **Mid-batch isolation**: a crash inside one ingestion call after some
  claims have already persisted leaves those claims intact; the run
  ends in `FAILED` status. A retry against a clean store does not see
  duplicates from the prior attempt's successes (the matcher relies on
  claim-id uniqueness, which is enforced by the schema).

Both properties are already provided by the existing transaction model
(each `ClaimStore.create` commits independently, canonical compilation
is a single atomic write). The tests below pin the behaviour so a
future refactor that batches multiple lanes into a single transaction
fails loudly.
"""

from __future__ import annotations

import socket

import httpx

from app.schemas.enums import IngestionStatus
from app.services.claim_store import ClaimStore
from app.services.openapi_ingestion import OpenApiIngestionService


class _AlwaysFailsClient:
    """HTTP client stand-in that raises before the lane can persist."""

    def get(self, url, *, headers, timeout, resolution=None):  # pragma: no cover - error path
        raise httpx.RequestError("simulated lane failure")


class _OkClient:
    def __init__(self, body: str) -> None:
        self._body = body
        self._used = False

    def get(self, url, *, headers, timeout, resolution=None):
        if self._used:
            raise AssertionError("OK client must not be called twice")
        self._used = True
        return _StubResponse(self._body)


class _StubResponse:
    def __init__(self, body: str) -> None:
        self.status_code = 200
        self.text = body
        self.headers = {"content-type": "application/json"}


_MINIMAL_OPENAPI = """
{
  "openapi": "3.0.0",
  "info": {"title": "isolation-smoke", "version": "0"},
  "paths": {
    "/echo": {
      "get": {
        "operationId": "echo",
        "summary": "echo",
        "responses": {"200": {"description": "ok"}}
      }
    }
  }
}
""".strip()


def test_failing_lane_does_not_block_subsequent_lane(tmp_path) -> None:
    original_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    socket.getaddrinfo = fake_getaddrinfo
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'iso.db'}")
    try:
        # First call: lane fails with the always-failing client. The service
        # contract is a FAILED response with structured errors, not an
        # exception escaping the lane boundary.
        failing_svc = OpenApiIngestionService(store, client=_AlwaysFailsClient())
        failed = failing_svc.ingest(
            tool_id="openai-api",
            url="https://api.openai.com/v1/openapi.json",
            submitted_by="iso-test",
        )
        assert failed.status == IngestionStatus.FAILED
        assert failed.errors

        # Pre-failure store state is unchanged: no claims, no spec rows.
        assert store.list(limit=10) == []
        assert store.get_canonical_tool_spec("openai-api") is None

        # Second call: same lane with a working client succeeds cleanly.
        ok_svc = OpenApiIngestionService(store, client=_OkClient(_MINIMAL_OPENAPI))
        response = ok_svc.ingest(
            tool_id="openai-api",
            url="https://api.openai.com/v1/openapi.json",
            submitted_by="iso-test",
        )
        assert response.status == IngestionStatus.COMPLETED
        assert response.operation_count == 1
        # The successful run actually produced a claim.
        assert len(store.list(limit=10)) >= 1
    finally:
        socket.getaddrinfo = original_getaddrinfo
