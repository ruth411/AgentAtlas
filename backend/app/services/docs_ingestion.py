"""Stage 7b: Safe docs ingestion.

This module fetches documentation pages for the locked Stage 0 tools and
converts them into structured `official_docs` evidence + claims. The trust
boundary has four layers:

1. **Contract gate** — every URL must appear in
   `contracts/docs_ingestion_sources.v1.json` and its hostname must be in
   the per-tool `official_hosts` from `tool_trust_sources.v1.json`.
2. **Scheme + IP gate** — only `https://` URLs whose resolved hostname is
   NOT private / loopback / link-local / multicast / reserved are fetched.
3. **Redirect gate** — httpx auto-redirect is disabled; redirects are
   followed manually, and every hop is re-validated against the same scheme,
   host-allowlist, and IP-class checks.
4. **Sanitizer** — captured HTML is parsed; `script` / `style` / `iframe` /
   `object` / `embed` / `template` / `noscript` content is dropped entirely;
   inline `on*` event handlers are dropped; only collected text is stored
   as the excerpt. The raw response body is *also* persisted (for audit)
   but never exposed as evidence content.

The orchestrator path mirrors CLI ingestion: artifact → claim → verify.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cache
from hashlib import sha256
import html
from html.parser import HTMLParser
import json
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    IngestionArtifactType,
    IngestionStatus,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.schemas.ingestion import (
    DocsFetchCacheEntry,
    DocsIngestionResponse,
    IngestionRun,
    RawIngestionArtifact,
)
from app.services.claim_store import (
    ClaimStore,
    DuplicateClaimError,
    DuplicateEvidenceError,
    EvidencePolicyError,
    ToolNotAllowedError,
)
from app.services.evidence_trust import _trust_sources  # reuse the loader cache
from app.services.http_safety import (
    HttpFetchError,
    assert_url_is_safe,
    host_in_allowlist,
)
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_ingestion_artifact_id,
    generate_ingestion_run_id,
)
from app.services.contract_paths import contract_path
from app.services.orchestrator import CanonOrchestrator
from app.services.risk_classifier import classify_action


_DOCS_INGESTION_CONTRACT = contract_path("docs_ingestion_sources.v1.json")
_EXCERPT_MAX_CHARS = 8000
_SAFE_TAGS_DROP_TEXT = frozenset(
    {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg"}
)


class DocsIngestionError(ValueError):
    """Raised when a docs ingestion request is unsupported, unsafe, or fails."""


@dataclass(frozen=True)
class DocsFetchSpec:
    tool_id: str
    url: str
    claim_type: ClaimType
    subject: str
    evidence_type: EvidenceType
    timeout_seconds: int
    max_bytes: int
    cache_ttl_seconds: int
    max_redirects: int
    allowed_content_types: tuple[str, ...]


@dataclass(frozen=True)
class DocsFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    raw_body: str
    sanitized_text: str
    etag: str | None
    last_modified: str | None
    redirect_chain: tuple[str, ...]
    not_modified: bool


class DocsHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        """Issue a single GET. Must NOT follow redirects automatically."""


class HttpxDocsClient:
    """Production client. Disables automatic redirects."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)


class DocsIngestionService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        client: DocsHttpClient | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        self._client = client or HttpxDocsClient()
        self._now = now

    def ingest(
        self,
        *,
        tool_id: str,
        url: str,
        submitted_by: str = "docs-ingestion-agent",
        verify: bool = True,
    ) -> DocsIngestionResponse:
        spec = docs_fetch_spec(tool_id=tool_id, url=url)
        run_id = generate_ingestion_run_id()
        started_at = self._timestamp()
        run = IngestionRun(
            run_id=run_id,
            tool_id=spec.tool_id,
            command=spec.url,
            status=IngestionStatus.IN_PROGRESS,
            submitted_by=submitted_by,
            created_at=started_at,
            completed_at=None,
        )
        self._store.save_ingestion_run(run)

        errors: list[str] = []
        artifact_ids: list[str] = []
        created_claim_ids: list[str] = []
        verification_result_ids: list[str] = []
        cache_hit = False
        final_url = spec.url
        redirect_chain: tuple[str, ...] = ()
        content_length = 0

        try:
            cache_entry = self._store.get_docs_fetch_cache(spec.url)
            fetch = self._fetch(spec, cache_entry)
            final_url = fetch.final_url
            redirect_chain = fetch.redirect_chain
            cache_hit = fetch.not_modified

            if fetch.not_modified and cache_entry is not None and cache_entry.last_artifact_id:
                # Cache hit — reuse the prior artifact and create a new claim
                # pointing at it. No new raw_content row is needed.
                artifact = self._store.get_ingestion_artifact(cache_entry.last_artifact_id)
                if artifact is None:
                    raise DocsIngestionError(
                        "Cache hit but the prior artifact has been deleted; refetch required."
                    )
                content_length = len(artifact.raw_content)
                artifact_ids.append(artifact.artifact_id)
            else:
                captured_at = self._timestamp()
                artifact = _artifact_from_fetch(
                    run_id=run_id, result=fetch, captured_at=captured_at
                )
                self._store.save_ingestion_artifact(artifact)
                content_length = len(artifact.raw_content)
                artifact_ids.append(artifact.artifact_id)
                self._store.save_docs_fetch_cache(
                    DocsFetchCacheEntry(
                        url=spec.url,
                        etag=fetch.etag,
                        last_modified=fetch.last_modified,
                        body_hash=artifact.hash,
                        last_fetched_at=captured_at,
                        last_artifact_id=artifact.artifact_id,
                    )
                )

            claim = _claim_from_fetch(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                submitted_by=submitted_by,
                created_at=self._timestamp(),
            )
            self._store.create(claim)
            created_claim_ids.append(claim.claim_id)

            if verify:
                verification = CanonOrchestrator(self._store).verify_claim(claim)
                saved = self._store.save_verification_result(verification)
                verification_result_ids.append(saved.verification_id)
        except (
            DocsIngestionError,
            DuplicateClaimError,
            DuplicateEvidenceError,
            EvidencePolicyError,
            ToolNotAllowedError,
            ValueError,
        ) as exc:
            errors.append(str(exc))

        completed_at = self._timestamp()
        status = IngestionStatus.COMPLETED if not errors else IngestionStatus.FAILED
        # IngestionRun.summary is typed CliIngestionSummary (shared across
        # ingestion lanes). Docs-specific fields (`final_url`, `redirect_chain`,
        # `content_length`) are surfaced on `DocsIngestionResponse` so callers
        # can audit redirect behavior and cache reuse without joining tables.
        self._store.save_ingestion_run(
            run.model_copy(
                update={
                    "status": status,
                    "completed_at": completed_at,
                    "summary": run.summary.model_copy(
                        update={
                            "capture_argv": [spec.url],
                            "created_claim_ids": created_claim_ids,
                            "verification_result_ids": verification_result_ids,
                            "artifact_ids": artifact_ids,
                            "errors": errors,
                        }
                    ),
                }
            )
        )
        return DocsIngestionResponse(
            run_id=run_id,
            status=status,
            cache_hit=cache_hit,
            final_url=final_url,
            redirect_chain=list(redirect_chain),
            content_length=content_length,
            created_claim_ids=created_claim_ids,
            verification_result_ids=verification_result_ids,
            artifact_ids=artifact_ids,
            errors=errors,
        )

    def ingest_all_for_tool(
        self,
        *,
        tool_id: str,
        submitted_by: str = "docs-ingestion-agent",
        verify: bool = True,
    ) -> list[DocsIngestionResponse]:
        urls = list_docs_sources_for_tool(tool_id)
        if not urls:
            raise DocsIngestionError(
                f"Docs ingestion is not allowlisted for tool '{tool_id}'."
            )
        return [
            self.ingest(tool_id=tool_id, url=url, submitted_by=submitted_by, verify=verify)
            for url in urls
        ]

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def _fetch(
        self,
        spec: DocsFetchSpec,
        cache_entry: DocsFetchCacheEntry | None,
    ) -> DocsFetchResult:
        """Fetch with manual redirect following and SSRF re-validation."""

        url = spec.url
        redirect_chain: list[str] = []
        for hop in range(spec.max_redirects + 1):
            try:
                assert_url_is_safe(
                    url, allowed_hosts=_official_hosts_for_tool(spec.tool_id)
                )
            except HttpFetchError as exc:
                raise DocsIngestionError(str(exc)) from exc
            headers: dict[str, str] = {
                "User-Agent": "AgentAtlas-DocsIngestion/1.0",
                "Accept": ", ".join(spec.allowed_content_types),
            }
            # Conditional request — server may answer 304 even if cache TTL
            # has not elapsed.
            if cache_entry is not None and url == spec.url:
                if cache_entry.etag:
                    headers["If-None-Match"] = cache_entry.etag
                if cache_entry.last_modified:
                    headers["If-Modified-Since"] = cache_entry.last_modified

            try:
                response = self._client.get(
                    url,
                    headers=headers,
                    timeout=float(spec.timeout_seconds),
                )
            except httpx.TimeoutException as exc:
                raise DocsIngestionError(
                    f"Docs fetch timed out after {spec.timeout_seconds}s."
                ) from exc
            except httpx.RequestError as exc:
                raise DocsIngestionError(f"Docs fetch failed: {exc}") from exc

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise DocsIngestionError(
                        f"Redirect response {response.status_code} missing Location header."
                    )
                if hop >= spec.max_redirects:
                    raise DocsIngestionError(
                        f"Docs fetch exceeded max_redirects={spec.max_redirects}."
                    )
                redirect_chain.append(location)
                url = location
                continue

            if response.status_code == 304:
                # Server confirms our cached body is current.
                return DocsFetchResult(
                    url=spec.url,
                    final_url=url,
                    status_code=304,
                    content_type=response.headers.get("content-type", ""),
                    raw_body="",
                    sanitized_text="",
                    etag=cache_entry.etag if cache_entry else None,
                    last_modified=cache_entry.last_modified if cache_entry else None,
                    redirect_chain=tuple(redirect_chain),
                    not_modified=True,
                )

            if response.status_code >= 400:
                raise DocsIngestionError(
                    f"Docs fetch returned HTTP {response.status_code} for {url}."
                )

            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and content_type not in spec.allowed_content_types:
                raise DocsIngestionError(
                    f"Docs fetch returned disallowed content-type {content_type!r}."
                )

            raw_body = response.text
            if len(raw_body.encode("utf-8")) > spec.max_bytes:
                raise DocsIngestionError(
                    f"Docs fetch exceeded max_bytes={spec.max_bytes}."
                )

            if not raw_body.strip():
                raise DocsIngestionError("Docs fetch returned empty body.")

            sanitized = _sanitize_html_to_text(raw_body)
            if not sanitized.strip():
                # The page may have been pure script/style; refuse rather than
                # publish an empty excerpt.
                raise DocsIngestionError(
                    "Docs fetch produced no safe text content after sanitization."
                )

            return DocsFetchResult(
                url=spec.url,
                final_url=url,
                status_code=response.status_code,
                content_type=content_type,
                raw_body=raw_body,
                sanitized_text=sanitized[:_EXCERPT_MAX_CHARS],
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                redirect_chain=tuple(redirect_chain),
                not_modified=False,
            )

        raise DocsIngestionError(
            f"Docs fetch exhausted redirects without a final response for {spec.url}."
        )


# -------- Contract loading and lookups --------


def docs_fetch_spec(*, tool_id: str, url: str) -> DocsFetchSpec:
    normalized_tool_id = tool_id.strip().casefold()
    data = _docs_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        raise DocsIngestionError(
            f"Docs ingestion is not allowlisted for tool '{tool_id}'."
        )

    for item in tool["sources"]:
        if item["url"] != url:
            continue
        return DocsFetchSpec(
            tool_id=normalized_tool_id,
            url=item["url"],
            claim_type=ClaimType(item["claim_type"]),
            subject=item["subject"],
            evidence_type=EvidenceType(item["evidence_type"]),
            timeout_seconds=int(data["default_timeout_seconds"]),
            max_bytes=int(data["default_max_bytes"]),
            cache_ttl_seconds=int(data["default_cache_ttl_seconds"]),
            max_redirects=int(data["max_redirects"]),
            allowed_content_types=tuple(data["allowed_content_types"]),
        )

    raise DocsIngestionError(
        f"URL '{url}' is not allowlisted for docs ingestion of tool '{tool_id}'."
    )


def list_docs_sources_for_tool(tool_id: str) -> list[str]:
    normalized_tool_id = tool_id.strip().casefold()
    data = _docs_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        return []
    return [str(item["url"]) for item in tool["sources"]]


@cache
def _docs_ingestion_sources() -> dict[str, Any]:
    with _DOCS_INGESTION_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_docs_ingestion_sources(data)
    return data


def _validate_docs_ingestion_sources(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("Docs ingestion source contract must have version=1")
    for key in (
        "default_timeout_seconds",
        "default_max_bytes",
        "default_cache_ttl_seconds",
        "max_redirects",
        "allowed_content_types",
        "tools",
    ):
        if key not in data:
            raise ValueError(f"Docs ingestion source contract missing {key}")

    if not isinstance(data["default_timeout_seconds"], int) or data["default_timeout_seconds"] <= 0:
        raise ValueError("default_timeout_seconds must be a positive int")
    if not isinstance(data["default_max_bytes"], int) or data["default_max_bytes"] <= 0:
        raise ValueError("default_max_bytes must be a positive int")
    if not isinstance(data["max_redirects"], int) or data["max_redirects"] < 0:
        raise ValueError("max_redirects must be a non-negative int")
    if not isinstance(data["allowed_content_types"], list) or not data["allowed_content_types"]:
        raise ValueError("allowed_content_types must be a non-empty list")

    tools = data["tools"]
    if not isinstance(tools, dict) or not tools:
        raise ValueError("tools must be a non-empty mapping")

    trust = _trust_sources()
    for tool_id, tool in tools.items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("invalid tool id in docs ingestion sources")
        if tool_id not in trust["tools"]:
            raise ValueError(
                f"docs ingestion tool '{tool_id}' is not in tool_trust_sources contract"
            )
        official_hosts = set(trust["tools"][tool_id]["official_hosts"])
        sources = tool.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"tool '{tool_id}' must define at least one source")
        for source in sources:
            for key in ("url", "claim_type", "subject", "evidence_type"):
                if key not in source:
                    raise ValueError(f"tool '{tool_id}' source missing {key}")
            url = source["url"]
            parsed = urlparse(url)
            if parsed.scheme != "https":
                raise ValueError(f"tool '{tool_id}' source url must be https")
            hostname = (parsed.hostname or "").lower()
            if not host_in_allowlist(hostname, official_hosts):
                raise ValueError(
                    f"tool '{tool_id}' source host {hostname!r} is not in official_hosts"
                )
            ClaimType(source["claim_type"])
            EvidenceType(source["evidence_type"])


def _official_hosts_for_tool(tool_id: str) -> frozenset[str]:
    trust = _trust_sources()
    tool = trust["tools"].get(tool_id)
    if not tool:
        return frozenset()
    return frozenset(tool["official_hosts"])


# Backwards-compat shim — older tests / external imports still reference
# `_assert_url_is_safe` here. The real implementation now lives in
# `app/services/http_safety.py` so the docs and openapi lanes share one
# auditable definition.
def _assert_url_is_safe(url: str, *, allowed_hosts: frozenset[str]) -> None:
    try:
        assert_url_is_safe(url, allowed_hosts=allowed_hosts)
    except HttpFetchError as exc:
        raise DocsIngestionError(str(exc)) from exc


# -------- HTML sanitizer (stdlib only) --------


class _SafeTextExtractor(HTMLParser):
    """Drop script/style/iframe/etc. content and on* attributes.

    Output is plain text only — collected from the data between non-droppable
    tags, with HTML entities decoded.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._suppress_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:  # type: ignore[override]
        if tag.lower() in _SAFE_TAGS_DROP_TEXT:
            self._suppress_depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag.lower() in _SAFE_TAGS_DROP_TEXT and self._suppress_depth > 0:
            self._suppress_depth -= 1

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._suppress_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        joined = " ".join(self._chunks)
        # Decode any straggler entities and collapse whitespace.
        joined = html.unescape(joined)
        return " ".join(joined.split())


def _sanitize_html_to_text(raw: str) -> str:
    parser = _SafeTextExtractor()
    parser.feed(raw)
    parser.close()
    return parser.text()


# -------- Build artifacts and claims --------


def _artifact_from_fetch(
    *,
    run_id: str,
    result: DocsFetchResult,
    captured_at: datetime,
) -> RawIngestionArtifact:
    artifact_id = generate_ingestion_artifact_id()
    source_uri = f"agentatlas://ingestion/{run_id}/artifacts/{artifact_id}"
    return RawIngestionArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=IngestionArtifactType.DOCS_CONTENT,
        source_uri=source_uri,
        raw_content=result.raw_body,
        hash=f"sha256:{sha256(result.raw_body.encode('utf-8')).hexdigest()}",
        captured_at=captured_at,
    )


def _claim_from_fetch(
    *,
    spec: DocsFetchSpec,
    artifact: RawIngestionArtifact,
    fetch: DocsFetchResult,
    submitted_by: str,
    created_at: datetime,
) -> KnowledgeClaim:
    # Evidence points at the original public URL the docs came from (this is
    # what the trust resolver inspects), not the agentatlas:// artifact URI.
    # That way trust = HIGH when the host is in the per-tool official_hosts
    # allowlist, regardless of how the ingester captured it.
    excerpt = fetch.sanitized_text if fetch.sanitized_text else artifact.raw_content[:_EXCERPT_MAX_CHARS]
    evidence = Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=spec.evidence_type,
        source_uri=fetch.final_url,
        excerpt=excerpt,
        hash=artifact.hash,
        captured_at=artifact.captured_at,
        trust_level=TrustLevel.HIGH,
    )
    risk = classify_action(spec.subject, tool_id=spec.tool_id).risk_level
    statement = _statement_from_text(excerpt, fallback=f"{spec.subject}: docs captured.")
    return KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=spec.claim_type,
        subject=spec.subject,
        statement=statement,
        tool_id=spec.tool_id,
        submitted_by=submitted_by,
        evidence=[evidence],
        risk_level=risk,
        created_at=created_at,
    )


_STATEMENT_MAX_CHARS = 480


def _statement_from_text(text: str, *, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_STATEMENT_MAX_CHARS]
    return fallback[:_STATEMENT_MAX_CHARS]


# Public surface — used by routes and tests.
__all__ = [
    "DocsIngestionError",
    "DocsIngestionService",
    "DocsHttpClient",
    "HttpxDocsClient",
    "DocsFetchSpec",
    "DocsFetchResult",
    "docs_fetch_spec",
    "list_docs_sources_for_tool",
    "_assert_url_is_safe",
    "_sanitize_html_to_text",
]


# Used to expose the cache TTL window for tests that want to assert freshness
# semantics without hitting the wall clock.
def _cache_is_fresh(entry: DocsFetchCacheEntry, *, now: datetime, ttl_seconds: int) -> bool:
    return (now - entry.last_fetched_at) <= timedelta(seconds=ttl_seconds)
