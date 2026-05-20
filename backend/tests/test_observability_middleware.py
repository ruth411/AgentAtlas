"""Stage 14: request-id + structured-log observability tests.

Two pinned behaviours:

- Every response carries an `X-Request-ID` header. If the client sent
  one, the server echoes it back; otherwise the server mints a fresh
  uuid4 hex string.
- Each request produces exactly one structured log line on the
  `ayiru.request` logger, with `event=http_request`, the
  request id, method, path, status_code, duration_ms, and (on 5xx) an
  `error_type` field.
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.claim_store import ClaimStore, get_claim_store


_HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture
def client(tmp_path):
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'obs.db'}")
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_response_includes_minted_request_id(client) -> None:
    response = client.get("/health")
    request_id = response.headers.get("X-Request-ID", "")
    assert _HEX_PATTERN.match(request_id), (
        f"expected uuid4 hex, got {request_id!r}"
    )


def test_request_id_is_echoed_from_client_header(client) -> None:
    response = client.get(
        "/health", headers={"X-Request-ID": "trace-abc-123"}
    )
    assert response.headers.get("X-Request-ID") == "trace-abc-123"


def test_structured_log_line_emitted_per_request(client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="ayiru.request")
    client.get("/health")

    matching = [
        record for record in caplog.records
        if record.name == "ayiru.request"
    ]
    assert len(matching) == 1, (
        f"expected exactly one log line; got {len(matching)}"
    )
    payload = json.loads(matching[0].getMessage())
    assert payload["event"] == "http_request"
    assert payload["method"] == "GET"
    assert payload["path"] == "/health"
    assert payload["status_code"] == 200
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0
    assert "request_id" in payload


def test_log_line_includes_v1_path(client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="ayiru.request")
    client.get("/v1/claims")

    matching = [
        record for record in caplog.records
        if record.name == "ayiru.request"
    ]
    payload = json.loads(matching[0].getMessage())
    assert payload["path"] == "/v1/claims"
    assert payload["status_code"] == 200


def test_legacy_path_log_line_records_legacy_url(client, caplog) -> None:
    """Even though the legacy mount adds a Deprecation header, the log
    line records the actual URL the client hit so ops can grep for
    legacy usage."""
    caplog.set_level(logging.INFO, logger="ayiru.request")
    client.get("/claims")

    matching = [
        record for record in caplog.records
        if record.name == "ayiru.request"
    ]
    payload = json.loads(matching[0].getMessage())
    assert payload["path"] == "/claims"
