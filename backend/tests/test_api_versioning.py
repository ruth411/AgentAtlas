"""Stage 14: API versioning + legacy-deprecation header tests.

Every existing router is mounted under `/v1/` AND at its legacy
un-versioned path for one release. Clients targeting `/v1/` get the
stable contract; clients still on the un-versioned mount receive a
`Deprecation: true` header (RFC 8594) pointing at the `/v1` successor.

These tests pin the dual-mount behaviour so a future refactor doesn't
silently remove one path or forget the deprecation header.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.claim_store import ClaimStore, get_claim_store


@pytest.fixture
def client(tmp_path):
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'v1.db'}")
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_v1_mount_serves_claims_listing(client) -> None:
    response = client.get("/v1/claims")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_legacy_mount_still_serves_claims_listing(client) -> None:
    """The un-versioned route stays alive for one release."""
    response = client.get("/claims")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_legacy_response_carries_deprecation_header(client) -> None:
    response = client.get("/claims")
    assert response.headers.get("Deprecation") == "true"
    assert "Sunset" in response.headers
    # Link header per RFC 8594 — point clients at the successor.
    link = response.headers.get("Link", "")
    assert 'rel="successor-version"' in link
    assert "/v1/claims" in link


def test_v1_response_has_no_deprecation_header(client) -> None:
    response = client.get("/v1/claims")
    assert "Deprecation" not in response.headers
    assert "Sunset" not in response.headers


def test_health_is_universal_no_versioning(client) -> None:
    """Health endpoints are never versioned — orchestration probes
    target a stable path regardless of API generation."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_v1_audit_events_endpoint_present(client) -> None:
    """Stage 13 routes are also dual-mounted."""
    response = client.get("/v1/audit/events")
    assert response.status_code == 200
    assert "events" in response.json()


def test_fastapi_app_version_matches_backend_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    assert app.version == project["version"]
