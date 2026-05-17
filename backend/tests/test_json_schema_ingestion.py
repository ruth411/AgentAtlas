"""Stage 7c.2: JSON Schema ingestion adversarial tests.

These tests pin every layer of the JSON Schema trust boundary: contract
gate, shared SSRF guard, redirect re-validation, content-type enforcement,
max-bytes, jsonschema meta-schema validation, per-top-level-property claim
generation including auxiliary deprecation claims, JSON Pointer (RFC 6901)
provenance, cache hit / miss, bulk ingest, and structured 422s from the API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import socket
from typing import Any, Iterable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes_ingestion import get_json_schema_client
from app.main import app
from app.schemas.enums import (
    ClaimType,
    IngestionArtifactType,
    IngestionStatus,
    TrustLevel,
)
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.http_safety import HttpFetchError, assert_url_is_safe
from app.services.json_schema_ingestion import (
    HttpxJsonSchemaClient,
    JsonSchemaIngestionError,
    JsonSchemaIngestionService,
    json_schema_fetch_spec,
    list_json_schema_sources_for_tool,
)


FIXED_TIME = datetime(2026, 5, 17, tzinfo=timezone.utc)
DOCKER_COMPOSE_URL = "https://json.schemastore.org/docker-compose.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")


def _minimal_doc(
    *,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    dialect: str = "http://json-schema.org/draft-07/schema#",
    title: str = "Docker Compose Configuration",
    schema_id: str = "https://json.schemastore.org/docker-compose.json",
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "$schema": dialect,
        "$id": schema_id,
        "title": title,
        "type": "object",
        "properties": properties
        if properties is not None
        else {
            "services": {"type": "object", "description": "Service definitions."},
            "version": {"type": "string"},
        },
    }
    if required is not None:
        doc["required"] = required
    return doc


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


class FakeJsonSchemaClient:
    def __init__(self, responses: Iterable[_FakeResponse | Exception]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self, url: str, *, headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        self.calls.append((url, dict(headers)))
        if not self._responses:
            raise AssertionError(
                f"FakeJsonSchemaClient out of scripted responses for {url}"
            )
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _bypass_dns_to_public(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _doc_response(doc: dict[str, Any], **headers: str) -> _FakeResponse:
    merged = {"content-type": "application/json"}
    merged.update(headers)
    return _FakeResponse(text=json.dumps(doc), headers=merged)


# ---------------------------------------------------------------------------
# Contract gate
# ---------------------------------------------------------------------------


def test_contract_resolves_known_url_for_known_tool() -> None:
    spec = json_schema_fetch_spec(tool_id="docker", url=DOCKER_COMPOSE_URL)
    assert spec.tool_id == "docker"
    assert spec.timeout_seconds > 0
    assert spec.max_bytes > 0
    assert spec.max_fields_per_schema > 0
    assert "application/json" in spec.allowed_content_types
    assert spec.subject_prefix


def test_contract_rejects_unknown_tool() -> None:
    with pytest.raises(JsonSchemaIngestionError):
        json_schema_fetch_spec(
            tool_id="nonexistent", url="https://json.schemastore.org/x.json"
        )


def test_contract_rejects_unlisted_url_for_known_tool() -> None:
    with pytest.raises(JsonSchemaIngestionError):
        json_schema_fetch_spec(
            tool_id="docker", url="https://json.schemastore.org/something-else.json"
        )


def test_list_json_schema_sources_for_tool_returns_contract_urls() -> None:
    urls = list_json_schema_sources_for_tool("docker")
    assert DOCKER_COMPOSE_URL in urls


def test_list_json_schema_sources_for_unknown_tool_returns_empty() -> None:
    assert list_json_schema_sources_for_tool("nonexistent") == []


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://json.schemastore.org/docker-compose.json",  # not https
        "ftp://json.schemastore.org/docker-compose.json",
        "https://attacker.example.com/docker-compose.json",  # not in allowlist
        "https:///docker-compose.json",  # no host
    ],
)
def test_ssrf_guard_rejects_bad_schemes_and_hosts(url: str) -> None:
    with pytest.raises(HttpFetchError):
        assert_url_is_safe(url, allowed_hosts=frozenset({"json.schemastore.org"}))


def test_ssrf_guard_rejects_private_ip_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(HttpFetchError, match="private"):
        assert_url_is_safe(
            "https://internal.example.com/x.json",
            allowed_hosts=frozenset({"internal.example.com"}),
        )


def test_ssrf_guard_rejects_loopback_literal() -> None:
    with pytest.raises(HttpFetchError):
        assert_url_is_safe(
            "https://127.0.0.1/secret.json",
            allowed_hosts=frozenset({"127.0.0.1"}),
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ingest_creates_artifact_and_field_claims(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _doc_response(
            _minimal_doc(required=["services"]),
            etag='"v1"',
            **{"last-modified": "Wed, 14 May 2026 00:00:00 GMT"},
        )
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert resp.cache_hit is False
    assert resp.field_count == 2
    assert resp.schema_title.startswith("Docker Compose")
    assert resp.schema_id == "https://json.schemastore.org/docker-compose.json"
    assert resp.schema_dialect.startswith("http://json-schema.org/draft-07")
    assert len(resp.artifact_ids) == 1
    assert len(resp.created_claim_ids) == 2  # 2 fields, no deprecation

    artifact = store.get_ingestion_artifact(resp.artifact_ids[0])
    assert artifact is not None
    assert artifact.artifact_type == IngestionArtifactType.JSON_SCHEMA_DOC

    field_claims = [store.get(cid) for cid in resp.created_claim_ids]
    subjects = {c.subject for c in field_claims}
    assert "docker-compose.yml: services" in subjects
    assert "docker-compose.yml: version" in subjects

    services_claim = next(c for c in field_claims if c.subject.endswith("services"))
    assert services_claim.claim_type == ClaimType.CONFIG_FIELD_EXISTS
    assert "required field `services`" in services_claim.statement
    assert services_claim.evidence[0].source_uri.endswith("#/properties/services")


# ---------------------------------------------------------------------------
# Auxiliary deprecation claim
# ---------------------------------------------------------------------------


def test_deprecated_field_emits_deprecation_claim(store: ClaimStore) -> None:
    doc = _minimal_doc(
        properties={
            "version": {"type": "string", "deprecated": True},
            "services": {"type": "object"},
        }
    )
    client = FakeJsonSchemaClient([_doc_response(doc)])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    kinds = {
        store.get(cid).claim_type  # type: ignore[union-attr]
        for cid in resp.created_claim_ids
    }
    assert ClaimType.CONFIG_FIELD_EXISTS in kinds
    assert ClaimType.FEATURE_DEPRECATED in kinds


def test_json_pointer_escapes_special_chars(store: ClaimStore) -> None:
    doc = _minimal_doc(
        properties={
            "weird~name": {"type": "string"},
            "ok": {"type": "string"},
        }
    )
    client = FakeJsonSchemaClient([_doc_response(doc)])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    weird = next(
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).subject.endswith("weird~name")  # type: ignore[union-attr]
    )
    assert weird.evidence[0].source_uri.endswith("#/properties/weird~0name")


def test_required_vs_optional_reflected_in_statement(store: ClaimStore) -> None:
    doc = _minimal_doc(
        properties={"a": {"type": "string"}, "b": {"type": "string"}},
        required=["a"],
    )
    client = FakeJsonSchemaClient([_doc_response(doc)])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    claims = [store.get(cid) for cid in resp.created_claim_ids]
    by_field = {c.subject.rsplit(": ", 1)[-1]: c for c in claims}
    assert "required field `a`" in by_field["a"].statement
    assert "optional field `b`" in by_field["b"].statement


# ---------------------------------------------------------------------------
# Evidence trust resolution
# ---------------------------------------------------------------------------


def test_evidence_from_aggregator_host_resolves_to_high_trust(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([_doc_response(_minimal_doc())])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    claim = store.get(resp.created_claim_ids[0])
    # The persisted evidence trust level should remain HIGH because
    # schemastore is in schema_aggregator_hosts.json_schema, even though
    # docker's own official_hosts is `docs.docker.com`.
    assert claim is not None
    assert claim.evidence[0].trust_level == TrustLevel.HIGH


# ---------------------------------------------------------------------------
# Cache + revalidation
# ---------------------------------------------------------------------------


def test_304_not_modified_reuses_cached_artifact(store: ClaimStore) -> None:
    first_client = FakeJsonSchemaClient([_doc_response(_minimal_doc(), etag='"v1"')])
    first = JsonSchemaIngestionService(store, client=first_client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert first.cache_hit is False
    assert len(first.artifact_ids) == 1

    revalidation = FakeJsonSchemaClient([
        _FakeResponse(
            status_code=304, text="", headers={"content-type": "application/json"}
        )
    ])
    second = JsonSchemaIngestionService(
        store, client=revalidation, now=FIXED_TIME + timedelta(hours=1)
    ).ingest(tool_id="docker", url=DOCKER_COMPOSE_URL)
    assert second.cache_hit is True
    assert second.artifact_ids == first.artifact_ids
    assert second.created_claim_ids != first.created_claim_ids
    assert revalidation.calls[0][1].get("If-None-Match") == '"v1"'


# ---------------------------------------------------------------------------
# Redirect handling
# ---------------------------------------------------------------------------


def test_redirect_to_allowed_host_is_followed(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://json.schemastore.org/docker-compose.v2.json",
                "content-type": "application/json",
            },
            text="",
        ),
        _doc_response(_minimal_doc()),
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert client.calls[1][0] == "https://json.schemastore.org/docker-compose.v2.json"


def test_redirect_to_disallowed_host_is_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://attacker.example.com/payload.json",
                "content-type": "application/json",
            },
            text="",
        )
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("not in the allowed hosts" in err.lower() for err in resp.errors)


def test_too_many_redirects_rejected(store: ClaimStore) -> None:
    redirects = [
        _FakeResponse(
            status_code=302,
            headers={
                "location": f"https://json.schemastore.org/hop-{i}.json",
                "content-type": "application/json",
            },
            text="",
        )
        for i in range(10)
    ]
    client = FakeJsonSchemaClient(redirects)
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_redirects" in err for err in resp.errors)


def test_redirect_missing_location_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _FakeResponse(status_code=302, headers={"content-type": "application/json"}, text=""),
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("missing Location header" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Content type and size enforcement
# ---------------------------------------------------------------------------


def test_disallowed_content_type_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _FakeResponse(
            text=json.dumps(_minimal_doc()), headers={"content-type": "text/html"}
        )
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("disallowed content-type" in err for err in resp.errors)


def test_oversized_response_rejected(store: ClaimStore) -> None:
    doc = _minimal_doc()
    body = json.dumps(doc) + (" " * (4 * 1024 * 1024 + 1024))
    client = FakeJsonSchemaClient([
        _FakeResponse(text=body, headers={"content-type": "application/json"})
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_bytes" in err for err in resp.errors)


def test_empty_body_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _FakeResponse(text="   \n  ", headers={"content-type": "application/json"})
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("empty body" in err for err in resp.errors)


def test_malformed_json_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _FakeResponse(text="{not json", headers={"content-type": "application/json"})
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("not valid JSON" in err for err in resp.errors)


def test_non_dict_root_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([
        _FakeResponse(text="[]", headers={"content-type": "application/json"})
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("JSON object" in err for err in resp.errors)


def test_invalid_meta_schema_rejected_by_validator(store: ClaimStore) -> None:
    # Declares Draft-07, but `type` must be a string or array of strings;
    # an integer is illegal — meta-schema must reject.
    bad = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": 42,
        "properties": {"x": {"type": "string"}},
    }
    client = FakeJsonSchemaClient([
        _FakeResponse(text=json.dumps(bad), headers={"content-type": "application/json"})
    ])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("meta-schema validation" in err for err in resp.errors)


def test_doc_with_no_properties_rejected(store: ClaimStore) -> None:
    doc = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
    }
    client = FakeJsonSchemaClient([_doc_response(doc)])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("no top-level properties" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# HTTP error + transport failure handling
# ---------------------------------------------------------------------------


def test_http_error_status_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([_FakeResponse(status_code=500, text="oops")])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("HTTP 500" in err for err in resp.errors)


def test_transport_timeout_rejected(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([httpx.TimeoutException("simulated timeout")])
    resp = JsonSchemaIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="docker", url=DOCKER_COMPOSE_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("timed out" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------


def test_bulk_ingest_runs_every_allowlisted_url(store: ClaimStore) -> None:
    urls = list_json_schema_sources_for_tool("docker")
    client = FakeJsonSchemaClient(
        [_doc_response(_minimal_doc()) for _ in urls]
    )
    responses = JsonSchemaIngestionService(
        store, client=client, now=FIXED_TIME
    ).ingest_all_for_tool(tool_id="docker")
    assert len(responses) == len(urls)
    assert all(r.status == IngestionStatus.COMPLETED for r in responses)


def test_bulk_ingest_unknown_tool_raises(store: ClaimStore) -> None:
    with pytest.raises(JsonSchemaIngestionError):
        JsonSchemaIngestionService(
            store, client=FakeJsonSchemaClient([])
        ).ingest_all_for_tool(tool_id="nonexistent")


# ---------------------------------------------------------------------------
# Production client wiring
# ---------------------------------------------------------------------------


def test_httpx_json_schema_client_disables_redirects() -> None:
    import inspect

    src = inspect.getsource(HttpxJsonSchemaClient.get)
    assert "follow_redirects=False" in src


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_api_post_json_schema_creates_run_and_artifact(store: ClaimStore) -> None:
    client = FakeJsonSchemaClient([_doc_response(_minimal_doc())])
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_json_schema_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/json_schema",
                json={"tool_id": "docker", "url": DOCKER_COMPOSE_URL},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "completed"
            assert body["artifact_ids"]
            artifact_id = body["artifact_ids"][0]
            artifact = http.get(f"/ingestion/artifacts/{artifact_id}")
            assert artifact.status_code == 200
            assert artifact.json()["artifact_type"] == "json_schema_doc"
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_unknown_url_with_structured_error(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_json_schema_client] = lambda: FakeJsonSchemaClient([])
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/json_schema",
                json={
                    "tool_id": "docker",
                    "url": "https://json.schemastore.org/not-allowlisted.json",
                },
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "JSON_SCHEMA_INGESTION_FAILED"
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_runs_all_urls(store: ClaimStore) -> None:
    urls = list_json_schema_sources_for_tool("docker")
    client = FakeJsonSchemaClient(
        [_doc_response(_minimal_doc()) for _ in urls]
    )
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_json_schema_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/json_schema/tools/docker")
            assert response.status_code == 200
            body = response.json()
            assert len(body) == len(urls)
            assert all(item["status"] == "completed" for item in body)
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_rejects_unknown_tool(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_json_schema_client] = lambda: FakeJsonSchemaClient([])
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/json_schema/tools/nonexistent")
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "JSON_SCHEMA_INGESTION_FAILED"
    finally:
        app.dependency_overrides.clear()
