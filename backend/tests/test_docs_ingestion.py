"""Stage 7b: Docs ingestion adversarial tests.

These tests pin every layer of the trust boundary: contract gate, SSRF guard,
redirect re-validation, sanitization, content-type enforcement, max-bytes,
cache hit / miss, bulk ingest, structured 422s from the API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import socket
from typing import Iterable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes_ingestion import get_docs_client
from app.main import app
from app.schemas.enums import IngestionArtifactType, IngestionStatus
from app.schemas.ingestion import DocsFetchCacheEntry
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.docs_ingestion import (
    DocsIngestionError,
    DocsIngestionService,
    _assert_url_is_safe,
    _find_page_chrome_markers,
    _sanitize_html_to_text,
    docs_fetch_spec,
    list_docs_sources_for_tool,
)


FIXED_TIME = datetime(2026, 5, 16, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


class _FakeResponse:
    """Minimal httpx.Response substitute."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "<html><body><h1>Hello</h1><p>git status documentation.</p></body></html>",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}


class FakeDocsClient:
    """Scripted client. Each call returns the next queued response. If the
    queue empties, raises so the test fails loudly rather than silently
    repeating the last reply."""

    def __init__(self, responses: Iterable[_FakeResponse | Exception]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        resolution=None,
    ) -> httpx.Response:
        self.calls.append((url, dict(headers)))
        if not self._responses:
            raise AssertionError(f"FakeDocsClient out of scripted responses for {url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _bypass_dns_to_public(monkeypatch):
    """Force socket.getaddrinfo to return a known public-class IP so SSRF
    resolution checks don't depend on real DNS in CI."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        # 93.184.216.34 is example.com's public address — guaranteed non-private.
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# Contract gate
# ---------------------------------------------------------------------------


def test_contract_resolves_known_url_for_known_tool() -> None:
    spec = docs_fetch_spec(tool_id="git", url="https://git-scm.com/docs/git-status")
    assert spec.tool_id == "git"
    assert spec.timeout_seconds > 0
    assert spec.max_bytes > 0


def test_contract_rejects_unknown_tool() -> None:
    with pytest.raises(DocsIngestionError):
        docs_fetch_spec(tool_id="nonexistent", url="https://x.example.com/")


def test_contract_rejects_unlisted_url_for_known_tool() -> None:
    with pytest.raises(DocsIngestionError):
        docs_fetch_spec(tool_id="git", url="https://git-scm.com/somewhere/else")


def test_list_docs_sources_for_tool_returns_contract_urls() -> None:
    urls = list_docs_sources_for_tool("git")
    assert "https://git-scm.com/docs/git-status" in urls
    assert "https://git-scm.com/docs/git-log" in urls


def test_list_docs_sources_for_unknown_tool_returns_empty() -> None:
    assert list_docs_sources_for_tool("nonexistent") == []


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://git-scm.com/docs/git-status",  # not https
        "ftp://git-scm.com/docs/git-status",
        "https://attacker.example.com/docs/git-status",  # not in allowlist
        "https:///docs/git-status",  # no host
    ],
)
def test_ssrf_guard_rejects_bad_schemes_and_hosts(url: str) -> None:
    with pytest.raises(DocsIngestionError):
        _assert_url_is_safe(url, allowed_hosts=frozenset({"git-scm.com"}))


def test_ssrf_guard_rejects_private_ip_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(DocsIngestionError, match="private"):
        _assert_url_is_safe(
            "https://internal.example.com/x", allowed_hosts=frozenset({"internal.example.com"})
        )


def test_ssrf_guard_rejects_loopback_literal() -> None:
    with pytest.raises(DocsIngestionError):
        _assert_url_is_safe(
            "https://127.0.0.1/secret", allowed_hosts=frozenset({"127.0.0.1"})
        )


def test_ssrf_guard_rejects_link_local_literal() -> None:
    with pytest.raises(DocsIngestionError):
        _assert_url_is_safe(
            "https://169.254.169.254/latest/meta-data/",
            allowed_hosts=frozenset({"169.254.169.254"}),
        )


def test_ssrf_guard_allows_public_resolved_host() -> None:
    # Backed by the autouse fixture which resolves to a known public IP.
    _assert_url_is_safe(
        "https://git-scm.com/docs/git-status",
        allowed_hosts=frozenset({"git-scm.com"}),
    )


# ---------------------------------------------------------------------------
# HTML sanitization
# ---------------------------------------------------------------------------


def test_sanitizer_strips_script_content() -> None:
    text = _sanitize_html_to_text(
        "<html><body><h1>Hi</h1><script>alert('x')</script><p>body</p></body></html>"
    )
    assert "alert" not in text
    assert "Hi" in text
    assert "body" in text


def test_sanitizer_strips_style_and_iframe_content() -> None:
    text = _sanitize_html_to_text(
        "<style>body { color: red }</style><iframe src='evil'>nope</iframe><p>kept</p>"
    )
    assert "color: red" not in text
    assert "nope" not in text
    assert "kept" in text


def test_sanitizer_decodes_html_entities() -> None:
    assert _sanitize_html_to_text("<p>5 &gt; 3 &amp; 2 &lt; 4</p>") == "5 > 3 & 2 < 4"


def test_sanitizer_strips_inline_event_handler_html() -> None:
    # Event handlers live in attributes, which the text extractor never emits.
    text = _sanitize_html_to_text('<a href="x" onclick="evil()">click</a>')
    assert "evil" not in text
    assert "click" in text


def test_sanitizer_collapses_whitespace() -> None:
    assert _sanitize_html_to_text("<p>foo\n\n\n   bar</p>") == "foo bar"


def test_sanitizer_drops_nav_header_footer_and_aside() -> None:
    """Regression for the 2026-06-12 audit. cli.github.com (and many
    other docs sites) wrap the page in <nav>/<header>/<footer>. Before
    this fix, that chrome flowed into the claim statement — the
    `gh auth login` claim was carrying *"GitHub CLI Take GitHub to the
    command line Skip to content…"* as its statement instead of the
    actual command description. The sanitizer now treats those tags
    the same way it treats `<script>` / `<style>`."""

    html_blob = """
    <html><body>
      <nav>Take GitHub to the command line Skip to content</nav>
      <header>CLI Manual Release notes</header>
      <p>gh auth login authenticates to GitHub.</p>
      <aside>Navigation sidebar links</aside>
      <footer>© GitHub 2026</footer>
    </body></html>
    """
    text = _sanitize_html_to_text(html_blob)
    assert "gh auth login authenticates to GitHub." in text
    assert "Skip to content" not in text
    assert "Release notes" not in text
    assert "Navigation sidebar" not in text
    assert "GitHub 2026" not in text


def test_sanitizer_prefers_main_content_when_present() -> None:
    """When the page has a `<main>` element, only text inside it survives.
    Catches sites that put nav-like chrome in plain <div> wrappers
    instead of semantic tags — the chrome lives outside <main> and gets
    discarded."""

    html_blob = """
    <html><body>
      <div class="navbar">Skip to content Manual Release notes</div>
      <main>
        <h1>gh auth login</h1>
        <p>Authenticate with a GitHub host. Required scope: repo.</p>
      </main>
      <div class="navbar-bottom">© 2026 GitHub Inc.</div>
    </body></html>
    """
    text = _sanitize_html_to_text(html_blob)
    assert "gh auth login" in text
    assert "Required scope: repo." in text
    assert "Skip to content" not in text
    assert "Manual Release notes" not in text
    assert "GitHub Inc." not in text


def test_sanitizer_supports_role_main() -> None:
    """`role='main'` is the accessibility equivalent of `<main>` — sites
    that use ARIA roles instead of semantic tags also benefit from the
    main-content restriction."""

    html_blob = """
    <body>
      <div>page chrome that should be dropped</div>
      <section role="main"><p>real content</p></section>
    </body>
    """
    text = _sanitize_html_to_text(html_blob)
    assert "real content" in text
    assert "page chrome" not in text


def test_sanitizer_falls_back_when_no_main_content_tag() -> None:
    """If the page has no `<main>` / `<article>` / `role='main'`, the
    extractor falls back to whole-body extraction (minus the
    always-dropped tags). Old / unstructured pages don't go silent."""

    html_blob = "<html><body><p>just a paragraph in a plain body</p></body></html>"
    text = _sanitize_html_to_text(html_blob)
    assert "just a paragraph in a plain body" in text


def test_sanitizer_strips_known_page_chrome_markers() -> None:
    text = _sanitize_html_to_text(
        "<p>terraform plan command reference | Terraform | HashiCorp Developer "
        "HashiConf 2025 Don't miss the live stream of HashiConf Day 2 happening now "
        "View live stream</p>"
    )
    assert "terraform plan command reference" in text
    assert "HashiConf 2025" not in text
    assert _find_page_chrome_markers(text) == []


# ---------------------------------------------------------------------------
# Ingestion happy path (with mock client)
# ---------------------------------------------------------------------------


def test_ingest_creates_artifact_and_claim_and_verifies(store: ClaimStore) -> None:
    response_body = (
        "<html><body><h1>git status</h1>"
        "<p>Shows the working tree status.</p>"
        "<script>alert('x')</script></body></html>"
    )
    client = FakeDocsClient([
        _FakeResponse(
            status_code=200,
            text=response_body,
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": '"abc123"',
                "last-modified": "Wed, 14 May 2026 00:00:00 GMT",
            },
        )
    ])
    service = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False)

    resp = service.ingest(tool_id="git", url="https://git-scm.com/docs/git-status")
    assert resp.status == IngestionStatus.COMPLETED
    assert resp.cache_hit is False
    assert len(resp.created_claim_ids) == 1
    assert len(resp.artifact_ids) == 1
    assert len(resp.verification_result_ids) == 1

    claim = store.get(resp.created_claim_ids[0])
    artifact = store.get_ingestion_artifact(resp.artifact_ids[0])
    cache = store.get_docs_fetch_cache("https://git-scm.com/docs/git-status")
    assert claim is not None
    assert artifact is not None
    assert artifact.artifact_type == IngestionArtifactType.DOCS_CONTENT
    assert cache is not None
    assert cache.etag == '"abc123"'
    assert cache.last_artifact_id == artifact.artifact_id
    # Sanitized excerpt must not contain the injected script.
    assert "alert" not in claim.evidence[0].excerpt
    # Statement is derived from sanitized first non-empty line.
    assert "git status" in claim.statement.lower()


# ---------------------------------------------------------------------------
# Cache + freshness
# ---------------------------------------------------------------------------


def test_304_not_modified_reuses_cached_artifact(store: ClaimStore) -> None:
    # First fetch warms the cache.
    initial = FakeDocsClient([
        _FakeResponse(
            text="<p>git status documentation, first revision.</p>",
            headers={"content-type": "text/html", "etag": '"v1"'},
        )
    ])
    first = DocsIngestionService(store, client=initial, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert first.cache_hit is False
    assert len(first.artifact_ids) == 1

    # Second fetch: server returns 304 because our ETag matches.
    revalidation = FakeDocsClient([
        _FakeResponse(status_code=304, text="", headers={"content-type": "text/html"})
    ])
    second = DocsIngestionService(
        store, client=revalidation, now=FIXED_TIME + timedelta(hours=1), compliance=False
    ).ingest(tool_id="git", url="https://git-scm.com/docs/git-status")
    assert second.cache_hit is True
    # Reused the same artifact_id from cache instead of creating a new one.
    assert second.artifact_ids == first.artifact_ids
    # But still produced a fresh claim (cache reuse is for the artifact only).
    assert second.created_claim_ids != first.created_claim_ids
    # The 304 response carried our If-None-Match header.
    assert revalidation.calls[0][1].get("If-None-Match") == '"v1"'


def test_304_without_cached_artifact_raises(store: ClaimStore) -> None:
    # Seed cache entry manually but skip the artifact persistence so the
    # cache-hit branch trips its safety check.
    store.save_docs_fetch_cache(
        DocsFetchCacheEntry(
            url="https://git-scm.com/docs/git-status",
            etag='"orphan"',
            last_modified=None,
            body_hash="sha256:" + sha256(b"x").hexdigest(),
            last_fetched_at=FIXED_TIME,
            last_artifact_id="art_nonexistent",
        )
    )
    client = FakeDocsClient([_FakeResponse(status_code=304, text="")])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("refetch required" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Redirect handling
# ---------------------------------------------------------------------------


def test_redirect_to_allowed_host_is_followed(store: ClaimStore) -> None:
    client = FakeDocsClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://git-scm.com/docs/git-status/v2",
                "content-type": "text/html",
            },
            text="",
        ),
        _FakeResponse(text="<p>v2 docs</p>"),
    ])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert len(client.calls) == 2
    assert client.calls[1][0] == "https://git-scm.com/docs/git-status/v2"


def test_relative_redirect_is_resolved_against_current_url(store: ClaimStore) -> None:
    """Regression for the Stage 20.8 imagemagick failure: when a server
    sends `Location: /relative/path`, urljoin must resolve it against the
    current absolute URL before the SSRF guard re-validates the next hop.
    Previously this raised "Only https:// URLs are allowed; got scheme ''"."""

    client = FakeDocsClient([
        _FakeResponse(
            status_code=302,
            headers={"location": "/docs/git-status-v2", "content-type": "text/html"},
            text="",
        ),
        _FakeResponse(text="<p>git-status v2 docs.</p>"),
    ])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.COMPLETED
    assert len(client.calls) == 2
    assert client.calls[1][0] == "https://git-scm.com/docs/git-status-v2"


def test_redirect_to_disallowed_host_is_rejected(store: ClaimStore) -> None:
    client = FakeDocsClient([
        _FakeResponse(
            status_code=301,
            headers={
                "location": "https://attacker.example.com/payload",
                "content-type": "text/html",
            },
            text="",
        )
    ])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("not in the allowed hosts" in err.lower() for err in resp.errors)


def test_too_many_redirects_rejected(store: ClaimStore) -> None:
    redirects = [
        _FakeResponse(
            status_code=302,
            headers={
                "location": f"https://git-scm.com/docs/hop-{i}",
                "content-type": "text/html",
            },
            text="",
        )
        for i in range(10)
    ]
    client = FakeDocsClient(redirects)
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_redirects" in err for err in resp.errors)


def test_redirect_response_missing_location_rejected(store: ClaimStore) -> None:
    client = FakeDocsClient([
        _FakeResponse(status_code=302, headers={"content-type": "text/html"}, text=""),
    ])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("missing Location header" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Content type and size enforcement
# ---------------------------------------------------------------------------


def test_disallowed_content_type_rejected(store: ClaimStore) -> None:
    client = FakeDocsClient([
        _FakeResponse(text="<p>x</p>", headers={"content-type": "application/javascript"}),
    ])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("disallowed content-type" in err for err in resp.errors)


def test_oversized_response_rejected(store: ClaimStore) -> None:
    # default_max_bytes is 4 MB (Stage 20.8 bump); use 5 MB to trip the limit.
    huge = "<p>" + ("x" * (5 * 1024 * 1024)) + "</p>"
    client = FakeDocsClient([_FakeResponse(text=huge, headers={"content-type": "text/html"})])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("max_bytes" in err for err in resp.errors)


def test_empty_body_rejected(store: ClaimStore) -> None:
    client = FakeDocsClient([_FakeResponse(text="   \n  ", headers={"content-type": "text/html"})])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("empty body" in err for err in resp.errors)


def test_response_with_only_script_content_rejected(store: ClaimStore) -> None:
    client = FakeDocsClient([
        _FakeResponse(
            text="<html><body><script>alert(1)</script></body></html>",
            headers={"content-type": "text/html"},
        )
    ])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("no safe text content" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# HTTP error + transport failure handling
# ---------------------------------------------------------------------------


def test_http_error_status_rejected(store: ClaimStore) -> None:
    client = FakeDocsClient([_FakeResponse(status_code=500, text="oops")])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("HTTP 500" in err for err in resp.errors)


def test_transport_timeout_rejected(store: ClaimStore) -> None:
    client = FakeDocsClient([httpx.TimeoutException("simulated timeout")])
    resp = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert resp.status == IngestionStatus.FAILED
    assert any("timed out" in err for err in resp.errors)


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------


def test_bulk_ingest_runs_every_allowlisted_url(store: ClaimStore) -> None:
    # Two URLs configured for `git`: status + log. One response per URL.
    client = FakeDocsClient([
        _FakeResponse(text="<p>git status text</p>"),
        _FakeResponse(text="<p>git log text</p>"),
    ])
    responses = DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest_all_for_tool(
        tool_id="git"
    )
    assert len(responses) == 2
    assert all(r.status == IngestionStatus.COMPLETED for r in responses)


def test_bulk_ingest_unknown_tool_raises(store: ClaimStore) -> None:
    with pytest.raises(DocsIngestionError):
        DocsIngestionService(store, client=FakeDocsClient([]), compliance=False).ingest_all_for_tool(
            tool_id="nonexistent"
        )


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_api_post_docs_creates_run_and_artifact(store: ClaimStore) -> None:
    client = FakeDocsClient([
        _FakeResponse(text="<p>git status documentation</p>"),
    ])
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_docs_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/docs",
                json={"tool_id": "git", "url": "https://git-scm.com/docs/git-status"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "completed"
            assert body["artifact_ids"]
            artifact_id = body["artifact_ids"][0]
            artifact = http.get(f"/ingestion/artifacts/{artifact_id}")
            assert artifact.status_code == 200
            assert artifact.json()["artifact_type"] == "docs_content"
    finally:
        app.dependency_overrides.clear()


def test_api_rejects_unknown_url_with_structured_error(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_docs_client] = lambda: FakeDocsClient([])
    try:
        with TestClient(app) as http:
            response = http.post(
                "/ingestion/docs",
                json={"tool_id": "git", "url": "https://git-scm.com/not-allowlisted"},
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "DOCS_FETCH_FAILED"
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_runs_all_urls(store: ClaimStore) -> None:
    client = FakeDocsClient([
        _FakeResponse(text="<p>git status</p>"),
        _FakeResponse(text="<p>git log</p>"),
    ])
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_docs_client] = lambda: client
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/docs/tools/git")
            assert response.status_code == 200
            body = response.json()
            assert len(body) == 2
            assert all(item["status"] == "completed" for item in body)
    finally:
        app.dependency_overrides.clear()


def test_api_bulk_endpoint_rejects_unknown_tool(store: ClaimStore) -> None:
    app.dependency_overrides[get_claim_store] = lambda: store
    app.dependency_overrides[get_docs_client] = lambda: FakeDocsClient([])
    try:
        with TestClient(app) as http:
            response = http.post("/ingestion/docs/tools/nonexistent")
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "DOCS_FETCH_FAILED"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Stage 20.7 — ToS compliance (rate limit + robots.txt + User-Agent)
# ---------------------------------------------------------------------------


def test_user_agent_header_identifies_ayiru_bulk_ingester(store: ClaimStore) -> None:
    client = FakeDocsClient([_FakeResponse(text="<p>git status doc.</p>")])
    DocsIngestionService(store, client=client, now=FIXED_TIME, compliance=False).ingest(
        tool_id="git", url="https://git-scm.com/docs/git-status"
    )
    assert len(client.calls) == 1
    _, headers = client.calls[0]
    assert headers["User-Agent"] == (
        "Ayiru-Bulk-Ingestion/1.0 (+https://github.com/ruth411/ayiru)"
    )


def test_robots_txt_disallow_blocks_fetch(store: ClaimStore) -> None:
    robots_body = "User-agent: *\nDisallow: /docs/\n"
    client = FakeDocsClient([_FakeResponse(text="<p>should not reach.</p>")])
    service = DocsIngestionService(
        store,
        client=client,
        now=FIXED_TIME,
        compliance=True,
        robots_fetch=lambda host: robots_body,
        sleep=lambda _: None,
    )
    resp = service.ingest(tool_id="git", url="https://git-scm.com/docs/git-status")
    assert resp.status == IngestionStatus.FAILED
    assert any("robots.txt" in err for err in resp.errors)
    # The client was never called because the robots check failed first.
    assert client.calls == []


def test_robots_txt_allow_permits_fetch(store: ClaimStore) -> None:
    robots_body = "User-agent: *\nAllow: /\n"
    client = FakeDocsClient([_FakeResponse(text="<p>git status doc.</p>")])
    service = DocsIngestionService(
        store,
        client=client,
        now=FIXED_TIME,
        compliance=True,
        robots_fetch=lambda host: robots_body,
        sleep=lambda _: None,
    )
    resp = service.ingest(tool_id="git", url="https://git-scm.com/docs/git-status")
    assert resp.status == IngestionStatus.COMPLETED


def test_missing_robots_txt_permits_fetch(store: ClaimStore) -> None:
    client = FakeDocsClient([_FakeResponse(text="<p>git status doc.</p>")])
    service = DocsIngestionService(
        store,
        client=client,
        now=FIXED_TIME,
        compliance=True,
        robots_fetch=lambda host: None,  # no robots.txt
        sleep=lambda _: None,
    )
    resp = service.ingest(tool_id="git", url="https://git-scm.com/docs/git-status")
    assert resp.status == IngestionStatus.COMPLETED


def test_rate_limit_sleeps_between_same_host_fetches(store: ClaimStore) -> None:
    sleeps: list[float] = []
    tick = [100.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        tick[0] += seconds

    def fake_monotonic() -> float:
        return tick[0]

    client = FakeDocsClient([
        _FakeResponse(text="<p>git status doc.</p>"),
        _FakeResponse(text="<p>git log doc.</p>"),
    ])
    service = DocsIngestionService(
        store,
        client=client,
        now=FIXED_TIME,
        compliance=True,
        robots_fetch=lambda host: None,
        sleep=fake_sleep,
        monotonic=fake_monotonic,
        min_interval_per_host_seconds=1.0,
    )
    service.ingest(tool_id="git", url="https://git-scm.com/docs/git-status")
    service.ingest(tool_id="git", url="https://git-scm.com/docs/git-log")
    # First fetch: no prior timestamp, no sleep. Second fetch: same host, < 1s
    # later in fake time, so the limiter sleeps to fill the gap.
    assert sleeps == [1.0]


def test_rate_limit_does_not_sleep_for_first_fetch(store: ClaimStore) -> None:
    sleeps: list[float] = []
    client = FakeDocsClient([_FakeResponse(text="<p>git status doc.</p>")])
    service = DocsIngestionService(
        store,
        client=client,
        now=FIXED_TIME,
        compliance=True,
        robots_fetch=lambda host: None,
        sleep=lambda s: sleeps.append(s),
    )
    service.ingest(tool_id="git", url="https://git-scm.com/docs/git-status")
    assert sleeps == []
