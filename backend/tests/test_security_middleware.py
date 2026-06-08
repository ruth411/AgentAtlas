"""Transport-level security middleware tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.claim_store import ClaimStore, get_claim_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("AYIRU_TRUSTED_HOSTS", raising=False)
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'security.db'}")
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client, monkeypatch
    app.dependency_overrides.clear()


def test_security_headers_are_stamped_on_success(client) -> None:
    test_client, _ = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_trusted_hosts_disabled_by_default(client) -> None:
    test_client, _ = client
    response = test_client.get("/health", headers={"Host": "evil.example.com"})
    assert response.status_code == 200


def test_trusted_hosts_accept_exact_match(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_TRUSTED_HOSTS", "api.example.com")
    response = test_client.get("/health", headers={"Host": "api.example.com"})
    assert response.status_code == 200


def test_trusted_hosts_accept_wildcard_subdomain(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_TRUSTED_HOSTS", "*.example.com")
    response = test_client.get("/health", headers={"Host": "api.example.com"})
    assert response.status_code == 200


def test_trusted_hosts_reject_mismatch_with_structured_400(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_TRUSTED_HOSTS", "api.example.com")
    response = test_client.get("/health", headers={"Host": "evil.example.com"})
    assert response.status_code == 400
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    body = response.json()["error"]
    assert body["code"] == "HTTP_ERROR"
    assert body["details"]["host"] == "evil.example.com"
    assert body["details"]["allowed_hosts"] == ["api.example.com"]


def test_trusted_hosts_ignore_port_when_matching(client) -> None:
    test_client, monkeypatch = client
    monkeypatch.setenv("AYIRU_TRUSTED_HOSTS", "api.example.com")
    response = test_client.get("/health", headers={"Host": "api.example.com:8000"})
    assert response.status_code == 200
