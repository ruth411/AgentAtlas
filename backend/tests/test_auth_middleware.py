"""Stage 14: API-key auth + reviewer-registry tests.

Auth is opt-in via `AYIRU_API_KEY`. When unset (the dev default),
every request goes through unauthenticated — this preserves the demo
ergonomics. When set:

- Read endpoints (GET / HEAD / OPTIONS) stay public.
- Write endpoints (POST / PUT / PATCH / DELETE) require
  `Authorization: Bearer <key>`.
- The `/health`, `/docs`, `/redoc`, `/openapi.json` paths stay public
  regardless so orchestration probes don't need credentials.

Separately, the reviewer registry (`AYIRU_REVIEWER_REGISTRY`) gates
`POST /verification/human-review`. When set to a comma-separated list,
only matching `reviewer_id` values are accepted.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.claim_store import ClaimStore, get_claim_store
from tests.helpers import valid_sha256


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("AYIRU_API_KEY", raising=False)
    monkeypatch.delenv("AYIRU_REVIEWER_REGISTRY", raising=False)
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'auth.db'}")
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client, monkeypatch
    app.dependency_overrides.clear()


def _claim_payload(claim_id: str = "claim_auth_001") -> dict:
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
# Auth disabled (default)
# ---------------------------------------------------------------------------


def test_writes_pass_when_api_key_unset(client) -> None:
    test_client, _ = client
    response = test_client.post("/claims", json=_claim_payload())
    assert response.status_code == 201


def test_reads_pass_when_api_key_unset(client) -> None:
    test_client, _ = client
    assert test_client.get("/claims").status_code == 200


# ---------------------------------------------------------------------------
# Auth enabled
# ---------------------------------------------------------------------------


def test_writes_without_bearer_token_return_401(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_API_KEY", "secret-key")

    response = test_client.post("/claims", json=_claim_payload())
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
    body = response.json()["error"]
    assert body["code"] == "HTTP_ERROR"
    assert "Bearer" in body["message"]


def test_writes_with_wrong_token_return_401(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_API_KEY", "secret-key")

    response = test_client.post(
        "/claims",
        json=_claim_payload(),
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 401


def test_writes_with_correct_token_succeed(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_API_KEY", "secret-key")

    response = test_client.post(
        "/claims",
        json=_claim_payload(),
        headers={"Authorization": "Bearer secret-key"},
    )
    assert response.status_code == 201


def test_reads_remain_public_when_auth_enabled(client) -> None:
    """The point of Ayiru is to be queryable. Read endpoints stay
    open even when the write-side has a credential."""
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_API_KEY", "secret-key")

    assert test_client.get("/claims").status_code == 200
    assert test_client.get("/v1/claims").status_code == 200


def test_health_stays_public_when_auth_enabled(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_API_KEY", "secret-key")

    assert test_client.get("/health").status_code == 200


def test_v1_writes_also_require_token(client) -> None:
    """Auth applies to both mounts — the /v1 prefix isn't an escape."""
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_API_KEY", "secret-key")

    assert test_client.post("/v1/claims", json=_claim_payload()).status_code == 401


def test_wrong_auth_scheme_rejected(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_API_KEY", "secret-key")

    response = test_client.post(
        "/claims",
        json=_claim_payload(),
        headers={"Authorization": "Basic secret-key"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Reviewer registry
# ---------------------------------------------------------------------------


def test_unregistered_reviewer_rejected(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_REVIEWER_REGISTRY", "alice,bob")

    # Have to create the claim first; the registry only gates reviewers.
    assert test_client.post("/claims", json=_claim_payload()).status_code == 201

    response = test_client.post(
        "/verification/human-review",
        json={
            "claim_id": "claim_auth_001",
            "reviewer_id": "carol",
            "decision": "needs_changes",
        },
    )
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "HUMAN_REVIEW_FAILED"
    assert "carol" in body["message"]


def test_registered_reviewer_accepted(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_REVIEWER_REGISTRY", "alice,bob")

    assert test_client.post("/claims", json=_claim_payload()).status_code == 201

    response = test_client.post(
        "/verification/human-review",
        json={
            "claim_id": "claim_auth_001",
            "reviewer_id": "alice",
            "decision": "needs_changes",
        },
    )
    assert response.status_code == 200


def test_open_registry_accepts_any_reviewer(client) -> None:
    """When the env var is unset, any reviewer_id is accepted."""
    test_client, _ = client
    assert test_client.post("/claims", json=_claim_payload()).status_code == 201

    response = test_client.post(
        "/verification/human-review",
        json={
            "claim_id": "claim_auth_001",
            "reviewer_id": "anonymous-reviewer",
            "decision": "needs_changes",
        },
    )
    assert response.status_code == 200
