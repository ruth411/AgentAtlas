"""Stage 13 HTTP tests for the audit log query surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.enums import AuditEventType
from app.services.claim_store import ClaimStore, get_claim_store
from tests.helpers import valid_sha256


@pytest.fixture
def client(tmp_path):
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'audit-routes.db'}")
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client, store
    app.dependency_overrides.clear()


def _claim(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "claim_type": "cli_command_exists",
        "subject": "gh repo delete",
        "statement": "Deletes a GitHub repository.",
        "tool_id": "github-cli",
        "submitted_by": "cli-agent",
        "risk_level": "critical",
        "evidence": [
            {
                "evidence_id": f"ev_{claim_id}",
                "evidence_type": "official_docs",
                "source_uri": "https://docs.github.com/x",
                "excerpt": "deletes",
                "hash": valid_sha256(f"ev_{claim_id}"),
                "captured_at": "2026-05-18T00:00:00+00:00",
                "trust_level": "high",
            }
        ],
    }


# ---------------------------------------------------------------------------
# GET /audit/events
# ---------------------------------------------------------------------------


def test_events_endpoint_returns_recent_submission(client) -> None:
    test_client, _ = client
    test_client.post("/claims", json=_claim("claim_audit_routes_001"))

    response = test_client.get("/audit/events")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["events"][0]["event_type"] == AuditEventType.CLAIM_SUBMITTED.value
    assert payload["events"][0]["actor"] == "cli-agent"


def test_events_endpoint_supports_filters(client) -> None:
    test_client, _ = client
    test_client.post("/claims", json=_claim("claim_audit_filter_a"))
    test_client.post("/claims", json=_claim("claim_audit_filter_b"))

    response = test_client.get(
        "/audit/events",
        params={"entity_id": "claim_audit_filter_a", "entity_type": "claim"},
    )
    payload = response.json()
    assert payload["total"] == 1
    assert payload["events"][0]["entity_id"] == "claim_audit_filter_a"


def test_events_endpoint_pagination(client) -> None:
    test_client, _ = client
    for index in range(6):
        test_client.post("/claims", json=_claim(f"claim_audit_paginate_{index}"))

    response = test_client.get("/audit/events", params={"limit": 2, "offset": 4})
    payload = response.json()
    assert payload["total"] == 6
    assert len(payload["events"]) == 2
    assert payload["limit"] == 2
    assert payload["offset"] == 4


def test_events_endpoint_rejects_naive_after_param(client) -> None:
    test_client, _ = client
    response = test_client.get(
        "/audit/events",
        params={"after": "2026-05-18T00:00:00"},  # no tz offset
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "AUDIT_EVENT_QUERY_INVALID"


def test_events_endpoint_invalid_event_type_returns_422(client) -> None:
    test_client, _ = client
    response = test_client.get(
        "/audit/events", params={"event_type": "not_a_real_event_type"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /audit/claims/{claim_id}
# ---------------------------------------------------------------------------


def test_claim_history_endpoint_returns_chronological_events(client) -> None:
    test_client, _ = client
    test_client.post("/claims", json=_claim("claim_audit_history_001"))

    response = test_client.get("/audit/claims/claim_audit_history_001")
    assert response.status_code == 200
    payload = response.json()
    assert payload["claim_id"] == "claim_audit_history_001"
    assert payload["event_count"] == 1
    assert payload["events"][0]["event_type"] == AuditEventType.CLAIM_SUBMITTED.value


def test_claim_history_unknown_id_returns_404(client) -> None:
    test_client, _ = client
    response = test_client.get("/audit/claims/claim_nope")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "CLAIM_NOT_FOUND"


def test_claim_history_invalid_id_returns_422(client) -> None:
    test_client, _ = client
    # The path validator rejects identifiers that don't match the
    # `[A-Za-z0-9_.-]{1,128}` shape (FastAPI surfaces this as 422).
    response = test_client.get("/audit/claims/with spaces and !!!")
    assert response.status_code == 422
