"""Stage 7c.3: GraphQL SDL ingestion adversarial tests.

These tests pin every layer of the GraphQL trust boundary: contract gate,
shared SSRF guard, redirect re-validation, content-type enforcement,
max-bytes, SDL parse + type-system validation via `graphql-core`, per-root-
field claim generation including auxiliary side_effect / destructive_action /
feature_deprecated claims, per-method risk mapping, fragment-style provenance,
cache hit / miss, bulk ingest, and structured 422s from the API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import socket
from typing import Iterable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes_ingestion import get_graphql_client
from app.main import app
from app.schemas.enums import (
    ClaimType,
    IngestionArtifactType,
    IngestionStatus,
    RiskLevel,
    TrustLevel,
)
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.graphql_ingestion import (
    GraphqlIngestionError,
    GraphqlIngestionService,
    HttpxGraphqlClient,
    graphql_fetch_spec,
    list_graphql_sources_for_tool,
)
from app.services.http_safety import HttpFetchError, assert_url_is_safe


FIXED_TIME = datetime(2026, 5, 17, tzinfo=timezone.utc)
GITHUB_SDL_URL = "https://docs.github.com/public/fpt/schema.docs.graphql"


_BASIC_SDL = """
type Query {
  user(id: ID!): User
}
type User { id: ID! name: String }
"""

_FULL_SDL = """
type Query {
  user(id: ID!): User
  repository(name: String!): Repository
}
type Mutation {
  deleteUser(id: ID!): Boolean
  updateRepo(name: String!): Repository
  legacyMutation: Boolean @deprecated(reason: "use updateRepo")
}
type Subscription {
  userUpdates: User
}
type User { id: ID! name: String }
type Repository { name: String! }
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "application/graphql"}


class FakeGraphqlClient:
    def __init__(self, responses: Iterable[_FakeResponse | Exception]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self, url: str, *, headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        self.calls.append((url, dict(headers)))
        if not self._responses:
            raise AssertionError(
                f"FakeGraphqlClient out of scripted responses for {url}"
            )
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt  # type: ignore[return-value]


def _sdl_response(sdl: str, **headers: str) -> _FakeResponse:
    merged = {"content-type": "application/graphql"}
    merged.update(headers)
    return _FakeResponse(text=sdl, headers=merged)


@pytest.fixture(autouse=True)
def _bypass_dns_to_public(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# Contract gate
# ---------------------------------------------------------------------------


def test_contract_resolves_known_url_for_known_tool() -> None:
    spec = graphql_fetch_spec(tool_id="github-cli", url=GITHUB_SDL_URL)
    assert spec.tool_id == "github-cli"
    assert spec.timeout_seconds > 0
    assert spec.max_bytes > 0
    assert spec.max_root_fields_per_schema > 0
    assert "application/graphql" in spec.allowed_content_types
    assert "delete" in spec.destructive_field_name_prefixes
    assert spec.subject_prefix


def test_contract_rejects_unknown_tool() -> None:
    with pytest.raises(GraphqlIngestionError):
        graphql_fetch_spec(
            tool_id="nonexistent", url="https://docs.github.com/x.graphql"
        )


def test_contract_rejects_unlisted_url_for_known_tool() -> None:
    with pytest.raises(GraphqlIngestionError):
        graphql_fetch_spec(
            tool_id="github-cli", url="https://docs.github.com/something-else.graphql"
        )


def test_list_graphql_sources_for_tool_returns_contract_urls() -> None:
    urls = list_graphql_sources_for_tool("github-cli")
    assert GITHUB_SDL_URL in urls


def test_list_graphql_sources_for_unknown_tool_returns_empty() -> None:
    assert list_graphql_sources_for_tool("nonexistent") == []


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.github.com/x.graphql",  # not https
        "ftp://docs.github.com/x.graphql",
        "https://attacker.example.com/x.graphql",  # not in allowlist
        "https:///x.graphql",  # no host
    ],
)
def test_ssrf_guard_rejects_bad_schemes_and_hosts(url: str) -> None:
    with pytest.raises(HttpFetchError):
        assert_url_is_safe(url, allowed_hosts=frozenset({"docs.github.com"}))


def test_ssrf_guard_rejects_private_ip_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(HttpFetchError, match="private"):
        assert_url_is_safe(
            "https://internal.example.com/x.graphql",
            allowed_hosts=frozenset({"internal.example.com"}),
        )


def test_ssrf_guard_rejects_loopback_literal() -> None:
    with pytest.raises(HttpFetchError):
        assert_url_is_safe(
            "https://127.0.0.1/schema.graphql",
            allowed_hosts=frozenset({"127.0.0.1"}),
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ingest_creates_artifact_and_query_claim(store: ClaimStore) -> None:
    client = FakeGraphqlClient([
        _sdl_response(
            _BASIC_SDL,
            etag='"v1"',
            **{"last-modified": "Wed, 14 May 2026 00:00:00 GMT"},
        )
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert resp.cache_hit is False
    assert resp.query_field_count == 1
    assert resp.mutation_field_count == 0
    assert resp.subscription_field_count == 0
    assert len(resp.artifact_ids) == 1
    assert len(resp.created_claim_ids) == 1  # one Query field, no aux

    artifact = store.get_ingestion_artifact(resp.artifact_ids[0])
    assert artifact is not None
    assert artifact.artifact_type == IngestionArtifactType.GRAPHQL_SDL

    claim = store.get(resp.created_claim_ids[0])
    assert claim is not None
    assert claim.claim_type == ClaimType.API_ENDPOINT_EXISTS
    assert claim.subject.endswith("Query: user")
    assert claim.risk_level == RiskLevel.LOW
    assert claim.evidence[0].source_uri.endswith("#Query.user")


# ---------------------------------------------------------------------------
# Auxiliary claim generation (risk + side-effect + destructive + deprecated)
# ---------------------------------------------------------------------------


def test_full_sdl_emits_expected_claim_set(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert resp.query_field_count == 2
    assert resp.mutation_field_count == 3
    assert resp.subscription_field_count == 1

    claims = [store.get(cid) for cid in resp.created_claim_ids]
    kinds = {c.claim_type for c in claims}
    assert ClaimType.API_ENDPOINT_EXISTS in kinds
    assert ClaimType.SIDE_EFFECT in kinds
    assert ClaimType.DESTRUCTIVE_ACTION in kinds
    assert ClaimType.FEATURE_DEPRECATED in kinds


def test_query_fields_are_low_risk(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    query_claims = [
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).subject.split(":")[0].endswith("Query")  # type: ignore[union-attr]
    ]
    assert query_claims
    assert all(c.risk_level == RiskLevel.LOW for c in query_claims)


def test_non_destructive_mutation_is_high_risk_not_critical(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    primary = next(
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).subject.endswith("Mutation: updateRepo")  # type: ignore[union-attr]
        and store.get(cid).claim_type == ClaimType.API_ENDPOINT_EXISTS  # type: ignore[union-attr]
    )
    assert primary.risk_level == RiskLevel.HIGH


def test_destructive_mutation_is_critical_and_emits_destructive_claim(
    store: ClaimStore,
) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    delete_user_claims = [
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).subject.startswith(  # type: ignore[union-attr]
            "GitHub GraphQL API Mutation: deleteUser"
        )
    ]
    kinds = {c.claim_type for c in delete_user_claims}
    assert ClaimType.API_ENDPOINT_EXISTS in kinds
    assert ClaimType.SIDE_EFFECT in kinds
    assert ClaimType.DESTRUCTIVE_ACTION in kinds
    primary = next(
        c for c in delete_user_claims if c.claim_type == ClaimType.API_ENDPOINT_EXISTS
    )
    assert primary.risk_level == RiskLevel.CRITICAL


def test_mutation_emits_side_effect_claim(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    mutation_subjects = {
        store.get(cid).subject  # type: ignore[union-attr]
        for cid in resp.created_claim_ids
        if "Mutation: updateRepo" in store.get(cid).subject  # type: ignore[union-attr]
    }
    assert any("(side effect)" in s for s in mutation_subjects)


def test_deprecated_field_emits_deprecation_claim(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    deprecated = [
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).claim_type == ClaimType.FEATURE_DEPRECATED  # type: ignore[union-attr]
    ]
    assert len(deprecated) == 1
    assert "legacyMutation" in deprecated[0].statement
    assert "use updateRepo" in deprecated[0].statement


def test_subscription_field_is_low_risk(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    sub = next(
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).subject.endswith("Subscription: userUpdates")  # type: ignore[union-attr]
        and store.get(cid).claim_type == ClaimType.API_ENDPOINT_EXISTS  # type: ignore[union-attr]
    )
    assert sub.risk_level == RiskLevel.LOW


def test_provenance_fragment_uses_operation_dot_field(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_FULL_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    user_claim = next(
        store.get(cid)
        for cid in resp.created_claim_ids
        if store.get(cid).subject.endswith("Query: user")  # type: ignore[union-attr]
    )
    assert user_claim.evidence[0].source_uri.endswith("#Query.user")


# ---------------------------------------------------------------------------
# Evidence trust resolution
# ---------------------------------------------------------------------------


def test_evidence_from_official_host_resolves_to_high_trust(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_BASIC_SDL)])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    claim = store.get(resp.created_claim_ids[0])
    assert claim is not None
    assert claim.evidence[0].trust_level == TrustLevel.HIGH


# ---------------------------------------------------------------------------
# Cache + revalidation
# ---------------------------------------------------------------------------


def test_304_not_modified_reuses_cached_artifact(store: ClaimStore) -> None:
    first_client = FakeGraphqlClient([_sdl_response(_BASIC_SDL, etag='"v1"')])
    first = GraphqlIngestionService(store, client=first_client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert first.cache_hit is False
    assert len(first.artifact_ids) == 1

    revalidation = FakeGraphqlClient([
        _FakeResponse(
            status_code=304, text="", headers={"content-type": "application/graphql"}
        )
    ])
    second = GraphqlIngestionService(
        store, client=revalidation, now=FIXED_TIME + timedelta(hours=1)
    ).ingest(tool_id="github-cli", url=GITHUB_SDL_URL)
    assert second.status == IngestionStatus.COMPLETED
    assert second.errors == []
    assert second.cache_hit is True
    assert second.artifact_ids == first.artifact_ids
    # Cache reuse still produces fresh claims so audit chronology is preserved.
    assert second.created_claim_ids
    assert second.created_claim_ids != first.created_claim_ids
    assert len(second.created_claim_ids) == len(first.created_claim_ids)
    assert revalidation.calls[0][1].get("If-None-Match") == '"v1"'


# ---------------------------------------------------------------------------
# Redirect handling
# ---------------------------------------------------------------------------


def test_redirect_to_allowed_host_is_followed(store: ClaimStore) -> None:
    client = FakeGraphqlClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://docs.github.com/public/fpt/schema.v2.graphql",
                "content-type": "application/graphql",
            },
            text="",
        ),
        _sdl_response(_BASIC_SDL),
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert (
        client.calls[1][0]
        == "https://docs.github.com/public/fpt/schema.v2.graphql"
    )


def test_redirect_to_disallowed_host_is_rejected(store: ClaimStore) -> None:
    client = FakeGraphqlClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://attacker.example.com/payload.graphql",
                "content-type": "application/graphql",
            },
            text="",
        )
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("not in the allowed hosts" in err.lower() for err in resp.errors)


def test_too_many_redirects_rejected(store: ClaimStore) -> None:
    redirects = [
        _FakeResponse(
            status_code=302,
            headers={
                "location": f"https://docs.github.com/hop-{i}.graphql",
                "content-type": "application/graphql",
            },
            text="",
        )
        for i in range(10)
    ]
    client = FakeGraphqlClient(redirects)
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_redirects" in err for err in resp.errors)


def test_redirect_missing_location_rejected(store: ClaimStore) -> None:
    client = FakeGraphqlClient([
        _FakeResponse(
            status_code=302, headers={"content-type": "application/graphql"}, text=""
        ),
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("missing Location header" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Content-type, size, SDL validation
# ---------------------------------------------------------------------------


def test_disallowed_content_type_rejected(store: ClaimStore) -> None:
    client = FakeGraphqlClient([
        _FakeResponse(text=_BASIC_SDL, headers={"content-type": "text/html"})
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("disallowed content-type" in err for err in resp.errors)


def test_oversized_response_rejected(store: ClaimStore) -> None:
    body = _BASIC_SDL + " " * (8 * 1024 * 1024 + 1024)
    client = FakeGraphqlClient([
        _FakeResponse(text=body, headers={"content-type": "application/graphql"})
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_bytes" in err for err in resp.errors)


def test_empty_body_rejected(store: ClaimStore) -> None:
    client = FakeGraphqlClient([
        _FakeResponse(text="   \n  ", headers={"content-type": "application/graphql"})
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("empty body" in err for err in resp.errors)


def test_malformed_sdl_rejected(store: ClaimStore) -> None:
    bad_sdl = "type Query { user(id ID!): User }"  # missing : between arg name and type
    client = FakeGraphqlClient([
        _FakeResponse(text=bad_sdl, headers={"content-type": "application/graphql"})
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("SDL" in err for err in resp.errors)


def test_sdl_without_root_operations_rejected(store: ClaimStore) -> None:
    # Define a type but no Query/Mutation/Subscription roots.
    no_roots = "type User { id: ID! }"
    client = FakeGraphqlClient([
        _FakeResponse(text=no_roots, headers={"content-type": "application/graphql"})
    ])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    # graphql-core may reject this at parse time, or our explicit "no root
    # operations" guard may catch it. Either is acceptable.
    assert any(
        "no Query / Mutation / Subscription" in err or "SDL" in err
        for err in resp.errors
    )


# ---------------------------------------------------------------------------
# HTTP error + transport failure handling
# ---------------------------------------------------------------------------


def test_http_error_status_rejected(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_FakeResponse(status_code=500, text="oops")])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("HTTP 500" in err for err in resp.errors)


def test_transport_timeout_rejected(store: ClaimStore) -> None:
    client = FakeGraphqlClient([httpx.TimeoutException("simulated timeout")])
    resp = GraphqlIngestionService(store, client=client, now=FIXED_TIME).ingest(
        tool_id="github-cli", url=GITHUB_SDL_URL
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("timed out" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------


def test_bulk_ingest_runs_every_allowlisted_url(store: ClaimStore) -> None:
    urls = list_graphql_sources_for_tool("github-cli")
    client = FakeGraphqlClient([_sdl_response(_BASIC_SDL) for _ in urls])
    responses = GraphqlIngestionService(
        store, client=client, now=FIXED_TIME
    ).ingest_all_for_tool(tool_id="github-cli")
    assert len(responses) == len(urls)
    assert all(r.status == IngestionStatus.COMPLETED for r in responses)


def test_bulk_ingest_unknown_tool_raises(store: ClaimStore) -> None:
    with pytest.raises(GraphqlIngestionError):
        GraphqlIngestionService(
            store, client=FakeGraphqlClient([])
        ).ingest_all_for_tool(tool_id="nonexistent")


# ---------------------------------------------------------------------------
# Production client wiring
# ---------------------------------------------------------------------------


def test_httpx_graphql_client_disables_redirects() -> None:
    import inspect

    src = inspect.getsource(HttpxGraphqlClient.get)
    assert "follow_redirects=False" in src


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_api_post_graphql_creates_run_and_artifact(store: ClaimStore) -> None:
    client = FakeGraphqlClient([_sdl_response(_BASIC_SDL)])
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_graphql_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/graphql",
                json={"tool_id": "github-cli", "url": GITHUB_SDL_URL},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "completed"
            assert body["artifact_ids"]
            artifact_id = body["artifact_ids"][0]
            artifact = http.get(f"/ingestion/artifacts/{artifact_id}")
            assert artifact.status_code == 200
            assert artifact.json()["artifact_type"] == "graphql_sdl"
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_unknown_url_with_structured_error(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_graphql_client] = lambda: FakeGraphqlClient([])
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/graphql",
                json={
                    "tool_id": "github-cli",
                    "url": "https://docs.github.com/not-allowlisted.graphql",
                },
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "GRAPHQL_INGESTION_FAILED"
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_runs_all_urls(store: ClaimStore) -> None:
    urls = list_graphql_sources_for_tool("github-cli")
    client = FakeGraphqlClient([_sdl_response(_BASIC_SDL) for _ in urls])
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_graphql_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/graphql/tools/github-cli")
            assert response.status_code == 200
            body = response.json()
            assert len(body) == len(urls)
            assert all(item["status"] == "completed" for item in body)
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_rejects_unknown_tool(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_graphql_client] = lambda: FakeGraphqlClient([])
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/graphql/tools/nonexistent")
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "GRAPHQL_INGESTION_FAILED"
    finally:
        app.dependency_overrides.clear()
