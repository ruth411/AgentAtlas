"""Stage 7c.1: OpenAPI ingestion adversarial tests.

These tests pin every layer of the OpenAPI trust boundary: contract gate,
shared SSRF guard (via the openapi service), redirect re-validation,
content-type enforcement, max-bytes, OpenAPI schema validation, per-endpoint
claim generation including auxiliary claims (auth / side-effect /
destructive / deprecated), JSON Pointer (RFC 6901) provenance, cache hit /
miss, bulk ingest, and structured 422s from the API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import socket
from typing import Any, Iterable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes_ingestion import get_openapi_client
from app.main import app
from app.schemas.enums import (
    ClaimType,
    IngestionArtifactType,
    IngestionStatus,
)
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.http_safety import HttpFetchError, assert_url_is_safe
from app.services.openapi_ingestion import (
    HttpxOpenApiClient,
    OpenApiIngestionError,
    OpenApiIngestionService,
    list_openapi_sources_for_tool,
    openapi_fetch_spec,
)


FIXED_TIME = datetime(2026, 5, 17, tzinfo=timezone.utc)
OPENAI_URL = "https://api.openai.com/v1/openapi.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")


def _minimal_spec(
    *,
    paths: dict[str, dict[str, Any]] | None = None,
    security: list[dict[str, Any]] | None = None,
    info_title: str = "OpenAI API",
    info_version: str = "1.0.0",
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {"title": info_title, "version": info_version},
        "paths": paths
        if paths is not None
        else {
            "/v1/chat/completions": {
                "post": {
                    "summary": "Create a chat completion.",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    if security is not None:
        spec["security"] = security
    return spec


class _FakeResponse:
    """Minimal httpx.Response substitute."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "application/json"}


class FakeOpenApiClient:
    """Scripted client. Each call returns the next queued response; raises
    if the queue empties so tests fail loudly instead of silently replaying."""

    def __init__(self, responses: Iterable[_FakeResponse | Exception]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self, url: str, *, headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        self.calls.append((url, dict(headers)))
        if not self._responses:
            raise AssertionError(f"FakeOpenApiClient out of scripted responses for {url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _bypass_dns_to_public(monkeypatch):
    """Force socket.getaddrinfo to return a known public IP so SSRF
    resolution checks don't depend on real DNS in CI."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _spec_response(spec: dict[str, Any], **headers: str) -> _FakeResponse:
    merged = {"content-type": "application/json"}
    merged.update(headers)
    return _FakeResponse(text=json.dumps(spec), headers=merged)


# ---------------------------------------------------------------------------
# Contract gate
# ---------------------------------------------------------------------------


def test_contract_resolves_known_url_for_known_tool() -> None:
    spec = openapi_fetch_spec(tool_id="openai-api", url=OPENAI_URL)
    assert spec.tool_id == "openai-api"
    assert spec.timeout_seconds > 0
    assert spec.max_bytes > 0
    assert spec.max_endpoints_per_spec > 0
    assert "application/json" in spec.allowed_content_types


def test_contract_rejects_unknown_tool() -> None:
    with pytest.raises(OpenApiIngestionError):
        openapi_fetch_spec(tool_id="nonexistent", url="https://x.example.com/openapi.json")


def test_contract_rejects_unlisted_url_for_known_tool() -> None:
    with pytest.raises(OpenApiIngestionError):
        openapi_fetch_spec(
            tool_id="openai-api", url="https://api.openai.com/v1/somewhere/else.json"
        )


def test_list_openapi_sources_for_tool_returns_contract_urls() -> None:
    urls = list_openapi_sources_for_tool("openai-api")
    assert OPENAI_URL in urls


def test_list_openapi_sources_for_unknown_tool_returns_empty() -> None:
    assert list_openapi_sources_for_tool("nonexistent") == []


# ---------------------------------------------------------------------------
# SSRF guard (exercised through the shared module the service depends on)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1/openapi.json",  # not https
        "ftp://api.openai.com/v1/openapi.json",
        "https://attacker.example.com/v1/openapi.json",  # not in allowlist
        "https:///v1/openapi.json",  # no host
    ],
)
def test_ssrf_guard_rejects_bad_schemes_and_hosts(url: str) -> None:
    with pytest.raises(HttpFetchError):
        assert_url_is_safe(url, allowed_hosts=frozenset({"api.openai.com"}))


def test_ssrf_guard_rejects_private_ip_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(HttpFetchError, match="private"):
        assert_url_is_safe(
            "https://internal.example.com/spec.json",
            allowed_hosts=frozenset({"internal.example.com"}),
        )


def test_ssrf_guard_rejects_loopback_literal() -> None:
    with pytest.raises(HttpFetchError):
        assert_url_is_safe(
            "https://127.0.0.1/openapi.json",
            allowed_hosts=frozenset({"127.0.0.1"}),
        )


def test_ssrf_guard_rejects_link_local_literal() -> None:
    with pytest.raises(HttpFetchError):
        assert_url_is_safe(
            "https://169.254.169.254/latest/meta-data/",
            allowed_hosts=frozenset({"169.254.169.254"}),
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ingest_creates_artifact_and_endpoint_claim(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _spec_response(_minimal_spec(), etag='"abc"', **{"last-modified": "Wed, 14 May 2026 00:00:00 GMT"})
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert resp.cache_hit is False
    assert resp.operation_count == 1
    assert resp.spec_title == "OpenAI API"
    assert resp.spec_version == "1.0.0"
    assert len(resp.artifact_ids) == 1

    # Primary endpoint claim + side_effect aux for POST = 2 claims.
    assert len(resp.created_claim_ids) == 2

    artifact = store.get_ingestion_artifact(resp.artifact_ids[0])
    assert artifact is not None
    assert artifact.artifact_type == IngestionArtifactType.OPENAPI_SPEC

    endpoint = next(
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).claim_type == ClaimType.API_ENDPOINT_EXISTS  # type: ignore[union-attr]
    )
    assert endpoint is not None
    assert endpoint.subject == "POST /v1/chat/completions"
    assert endpoint.evidence[0].source_uri.endswith("#/paths/~1v1~1chat~1completions/post")


# ---------------------------------------------------------------------------
# Auxiliary claim generation
# ---------------------------------------------------------------------------


def test_delete_operation_emits_destructive_claim(store: ClaimStore) -> None:
    spec = _minimal_spec(
        paths={
            "/v1/files/{file_id}": {
                "parameters": [
                    {
                        "name": "file_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "delete": {
                    "summary": "Delete a file.",
                    "responses": {"200": {"description": "ok"}},
                },
            }
        }
    )
    client = FakeOpenApiClient([_spec_response(spec)])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    kinds = {
        store.get(cid).claim_type  # type: ignore[union-attr]
        for cid in resp.created_claim_ids
    }
    assert ClaimType.API_ENDPOINT_EXISTS in kinds
    assert ClaimType.DESTRUCTIVE_ACTION in kinds


def test_operation_with_spec_global_security_emits_auth_claim(store: ClaimStore) -> None:
    spec = _minimal_spec(
        security=[{"bearerAuth": []}],
        paths={
            "/v1/models": {
                "get": {"summary": "List models.", "responses": {"200": {"description": "ok"}}}
            }
        },
    )
    client = FakeOpenApiClient([_spec_response(spec)])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    kinds = {
        store.get(cid).claim_type  # type: ignore[union-attr]
        for cid in resp.created_claim_ids
    }
    assert ClaimType.AUTH_REQUIREMENT in kinds


def test_operation_local_security_overrides_spec_security(store: ClaimStore) -> None:
    # Global security present, but operation explicitly opts out with [].
    spec = _minimal_spec(
        security=[{"bearerAuth": []}],
        paths={
            "/v1/public": {
                "get": {
                    "summary": "Public endpoint.",
                    "security": [],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    )
    client = FakeOpenApiClient([_spec_response(spec)])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    kinds = {
        store.get(cid).claim_type  # type: ignore[union-attr]
        for cid in resp.created_claim_ids
    }
    assert ClaimType.AUTH_REQUIREMENT not in kinds


def test_deprecated_operation_emits_deprecation_claim(store: ClaimStore) -> None:
    spec = _minimal_spec(
        paths={
            "/v1/legacy": {
                "get": {
                    "summary": "Legacy endpoint.",
                    "deprecated": True,
                    "responses": {"200": {"description": "ok"}},
                }
            }
        }
    )
    client = FakeOpenApiClient([_spec_response(spec)])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    kinds = {
        store.get(cid).claim_type  # type: ignore[union-attr]
        for cid in resp.created_claim_ids
    }
    assert ClaimType.FEATURE_DEPRECATED in kinds


def test_post_emits_side_effect_claim(store: ClaimStore) -> None:
    client = FakeOpenApiClient([_spec_response(_minimal_spec())])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    kinds = {
        store.get(cid).claim_type  # type: ignore[union-attr]
        for cid in resp.created_claim_ids
    }
    assert ClaimType.SIDE_EFFECT in kinds


def test_json_pointer_escapes_special_chars(store: ClaimStore) -> None:
    spec = _minimal_spec(
        paths={
            "/v1/items/{a~b}": {
                "parameters": [
                    {
                        "name": "a~b",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {
                    "summary": "Quirky path.",
                    "responses": {"200": {"description": "ok"}},
                },
            }
        }
    )
    client = FakeOpenApiClient([_spec_response(spec)])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    endpoint = next(
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).claim_type == ClaimType.API_ENDPOINT_EXISTS  # type: ignore[union-attr]
    )
    # ~ -> ~0, then each / -> ~1.
    assert endpoint.evidence[0].source_uri.endswith(
        "#/paths/~1v1~1items~1{a~0b}/get"
    )


# ---------------------------------------------------------------------------
# Cache + revalidation
# ---------------------------------------------------------------------------


def test_304_not_modified_reuses_cached_artifact(store: ClaimStore) -> None:
    first_client = FakeOpenApiClient([
        _spec_response(_minimal_spec(), etag='"v1"')
    ])
    first = OpenApiIngestionService(store, client=first_client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert first.cache_hit is False
    assert len(first.artifact_ids) == 1

    revalidation = FakeOpenApiClient([
        _FakeResponse(status_code=304, text="", headers={"content-type": "application/json"})
    ])
    second = OpenApiIngestionService(
        store, client=revalidation, now=FIXED_TIME + timedelta(hours=1)
    ).ingest(tool_id="openai-api", url=OPENAI_URL)
    # The 304 path must succeed (status COMPLETED), not silently fail because
    # an empty fetch.spec would trip the "no operations to ingest" guard.
    assert second.status == IngestionStatus.COMPLETED
    assert second.errors == []
    assert second.cache_hit is True
    assert second.artifact_ids == first.artifact_ids
    # Fresh claim ids even on 304 — cache reuse applies to the artifact only.
    assert second.created_claim_ids
    assert second.created_claim_ids != first.created_claim_ids
    assert len(second.created_claim_ids) == len(first.created_claim_ids)
    assert revalidation.calls[0][1].get("If-None-Match") == '"v1"'


# ---------------------------------------------------------------------------
# Redirect handling
# ---------------------------------------------------------------------------


def test_redirect_to_allowed_host_is_followed(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://api.openai.com/v1/openapi.v2.json",
                "content-type": "application/json",
            },
            text="",
        ),
        _spec_response(_minimal_spec()),
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert len(client.calls) == 2
    assert client.calls[1][0] == "https://api.openai.com/v1/openapi.v2.json"


def test_redirect_to_disallowed_host_is_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://attacker.example.com/payload.json",
                "content-type": "application/json",
            },
            text="",
        )
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("not in the allowed hosts" in err.lower() for err in resp.errors)


def test_too_many_redirects_rejected(store: ClaimStore) -> None:
    redirects = [
        _FakeResponse(
            status_code=302,
            headers={
                "location": f"https://api.openai.com/v1/hop-{i}.json",
                "content-type": "application/json",
            },
            text="",
        )
        for i in range(10)
    ]
    client = FakeOpenApiClient(redirects)
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_redirects" in err for err in resp.errors)


def test_redirect_response_missing_location_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _FakeResponse(status_code=302, headers={"content-type": "application/json"}, text=""),
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("missing Location header" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Content type and size enforcement
# ---------------------------------------------------------------------------


def test_disallowed_content_type_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _FakeResponse(
            text=json.dumps(_minimal_spec()),
            headers={"content-type": "text/html"},
        )
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("disallowed content-type" in err for err in resp.errors)


def test_oversized_response_rejected(store: ClaimStore) -> None:
    huge_paths = {
        f"/v1/x-{i}": {"get": {"responses": {"200": {"description": "ok"}}}}
        for i in range(200)
    }
    spec = _minimal_spec(paths=huge_paths)
    body = json.dumps(spec) + (" " * (8 * 1024 * 1024 + 1024))
    client = FakeOpenApiClient([
        _FakeResponse(text=body, headers={"content-type": "application/json"})
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_bytes" in err for err in resp.errors)


def test_empty_body_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _FakeResponse(text="   \n  ", headers={"content-type": "application/json"})
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("empty body" in err for err in resp.errors)


def test_malformed_json_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _FakeResponse(text="{not json", headers={"content-type": "application/json"})
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("not valid JSON" in err for err in resp.errors)


def test_non_dict_root_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([
        _FakeResponse(text="[]", headers={"content-type": "application/json"})
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("JSON object" in err for err in resp.errors)


def test_non_openapi_3_rejected(store: ClaimStore) -> None:
    bad = {"swagger": "2.0", "info": {"title": "x", "version": "1"}, "paths": {}}
    client = FakeOpenApiClient([
        _FakeResponse(text=json.dumps(bad), headers={"content-type": "application/json"})
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("OpenAPI 3.x" in err for err in resp.errors)


def test_invalid_openapi_schema_rejected_by_validator(store: ClaimStore) -> None:
    # Marked openapi=3.0.3 but missing required "info" -> validator must reject.
    bad = {"openapi": "3.0.3", "paths": {}}
    client = FakeOpenApiClient([
        _FakeResponse(text=json.dumps(bad), headers={"content-type": "application/json"})
    ])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("schema validation" in err for err in resp.errors)


def test_spec_with_no_operations_rejected(store: ClaimStore) -> None:
    spec = _minimal_spec(paths={})
    client = FakeOpenApiClient([_spec_response(spec)])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("no operations to ingest" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# HTTP error + transport failure handling
# ---------------------------------------------------------------------------


def test_http_error_status_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([_FakeResponse(status_code=500, text="oops")])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("HTTP 500" in err for err in resp.errors)


def test_transport_timeout_rejected(store: ClaimStore) -> None:
    client = FakeOpenApiClient([httpx.TimeoutException("simulated timeout")])
    resp = OpenApiIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="openai-api", url=OPENAI_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("timed out" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------


def test_bulk_ingest_runs_every_allowlisted_url(store: ClaimStore) -> None:
    urls = list_openapi_sources_for_tool("openai-api")
    client = FakeOpenApiClient(
        [_spec_response(_minimal_spec()) for _ in urls]
    )
    responses = OpenApiIngestionService(
        store, client=client, now=FIXED_TIME
    ).ingest_all_for_tool(tool_id="openai-api")
    assert len(responses) == len(urls)
    assert all(r.status == IngestionStatus.COMPLETED for r in responses)


def test_bulk_ingest_unknown_tool_raises(store: ClaimStore) -> None:
    with pytest.raises(OpenApiIngestionError):
        OpenApiIngestionService(store, client=FakeOpenApiClient([])).ingest_all_for_tool(
            tool_id="nonexistent"
        )


# ---------------------------------------------------------------------------
# Production client wiring
# ---------------------------------------------------------------------------


def test_httpx_openapi_client_disables_redirects() -> None:
    # Sanity-check the production client never follows redirects automatically
    # (so the service's manual SSRF revalidation can't be bypassed).
    import inspect

    src = inspect.getsource(HttpxOpenApiClient.get)
    assert "follow_redirects=False" in src


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_api_post_openapi_creates_run_and_artifact(store: ClaimStore) -> None:
    client = FakeOpenApiClient([_spec_response(_minimal_spec())])
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_openapi_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/openapi",
                json={"tool_id": "openai-api", "url": OPENAI_URL},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "completed"
            assert body["artifact_ids"]
            artifact_id = body["artifact_ids"][0]
            artifact = http.get(f"/ingestion/artifacts/{artifact_id}")
            assert artifact.status_code == 200
            assert artifact.json()["artifact_type"] == "openapi_spec"
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_unknown_url_with_structured_error(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_openapi_client] = lambda: FakeOpenApiClient([])
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/openapi",
                json={
                    "tool_id": "openai-api",
                    "url": "https://api.openai.com/v1/not-allowlisted.json",
                },
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "OPENAPI_INGESTION_FAILED"
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_runs_all_urls(store: ClaimStore) -> None:
    urls = list_openapi_sources_for_tool("openai-api")
    client = FakeOpenApiClient(
        [_spec_response(_minimal_spec()) for _ in urls]
    )
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_openapi_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/openapi/tools/openai-api")
            assert response.status_code == 200
            body = response.json()
            assert len(body) == len(urls)
            assert all(item["status"] == "completed" for item in body)
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_rejects_unknown_tool(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_openapi_client] = lambda: FakeOpenApiClient([])
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/openapi/tools/nonexistent")
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "OPENAPI_INGESTION_FAILED"
    finally:
        app.dependency_overrides.clear()
