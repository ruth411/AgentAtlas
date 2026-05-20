"""Stage 7c.1: Safe OpenAPI schema ingestion.

This module fetches OpenAPI 3.x specs from per-tool allowlisted URLs,
validates them against the OpenAPI meta-schema, and emits a structured
claim per (method, path) operation, plus auxiliary claims for:

- `auth_requirement` when the operation (or the spec-global default) carries
  a non-empty `security` block;
- `side_effect` for POST / PUT / PATCH operations;
- `destructive_action` for DELETE operations;
- `feature_deprecated` for operations with `deprecated: true`.

Every claim's evidence `source_uri` includes a JSON Pointer (RFC 6901) to
the exact node inside the spec that the claim was derived from, so an
auditor can trace any canonical assertion back to the byte that produced it.

The trust boundary mirrors Stage 7b: contract gate (URL must be in
`contracts/openapi_ingestion_sources.v1.json` and host must be in the
per-tool `official_hosts` from `tool_trust_sources.v1.json`), shared SSRF
guard, manual redirect re-validation, content-type allowlist
(`application/json` family), hard `max_bytes` + `timeout_seconds`, and
ETag / Last-Modified conditional revalidation via the existing
`docs_fetch_cache` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
import json
from typing import Any, Protocol

import httpx
from openapi_spec_validator import validate as openapi_validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    IngestionArtifactType,
    IngestionStatus,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.schemas.ingestion import (
    DocsFetchCacheEntry,
    IngestionRun,
    OpenApiIngestionResponse,
    RawIngestionArtifact,
)
from app.services.claim_store import (
    ClaimStore,
    DuplicateClaimError,
    DuplicateEvidenceError,
    EvidencePolicyError,
    ToolNotAllowedError,
)
from app.services.evidence_trust import _trust_sources
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


_OPENAPI_CONTRACT = contract_path("openapi_ingestion_sources.v1.json")

# HTTP methods recognised by OpenAPI 3.x.
_OPENAPI_METHODS: frozenset[str] = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_MUTATING_METHODS: frozenset[str] = frozenset({"post", "put", "patch"})
_DESTRUCTIVE_METHODS: frozenset[str] = frozenset({"delete"})

_STATEMENT_MAX_CHARS = 480
_SUBJECT_MAX_CHARS = 512


class OpenApiIngestionError(ValueError):
    """Raised when an OpenAPI ingestion request is unsupported, unsafe, or fails."""


# -------- Specs --------


@dataclass(frozen=True)
class OpenApiFetchSpec:
    tool_id: str
    url: str
    evidence_type: EvidenceType
    timeout_seconds: int
    max_bytes: int
    max_redirects: int
    max_endpoints_per_spec: int
    allowed_content_types: tuple[str, ...]


@dataclass(frozen=True)
class OpenApiFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    raw_body: str
    spec: dict[str, Any]
    etag: str | None
    last_modified: str | None
    redirect_chain: tuple[str, ...]
    not_modified: bool


# -------- HTTP client --------


class OpenApiHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        """Issue a single GET. Must NOT follow redirects automatically."""


class HttpxOpenApiClient:
    """Production client. Disables automatic redirects."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)


# -------- Service --------


class OpenApiIngestionService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        client: OpenApiHttpClient | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        self._client = client or HttpxOpenApiClient()
        self._now = now

    def ingest(
        self,
        *,
        tool_id: str,
        url: str,
        submitted_by: str = "openapi-ingestion-agent",
        verify: bool = True,
    ) -> OpenApiIngestionResponse:
        spec = openapi_fetch_spec(tool_id=tool_id, url=url)
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
        spec_title = ""
        spec_version = ""
        operation_count = 0
        skipped_operations: list[str] = []

        try:
            cache_entry = self._store.get_docs_fetch_cache(spec.url)
            fetch = self._fetch(spec, cache_entry)
            final_url = fetch.final_url
            redirect_chain = fetch.redirect_chain
            cache_hit = fetch.not_modified

            if fetch.not_modified and cache_entry is not None and cache_entry.last_artifact_id:
                artifact = self._store.get_ingestion_artifact(cache_entry.last_artifact_id)
                if artifact is None:
                    raise OpenApiIngestionError(
                        "Cache hit but the prior artifact has been deleted; refetch required."
                    )
                artifact_ids.append(artifact.artifact_id)
                # 304 means the server confirmed the body hasn't changed since
                # the cached artifact was captured; re-parse the cached body
                # so we can emit a fresh claim chain at the current timestamp.
                # Without this, `fetch.spec` is empty and the "no operations"
                # guard below would silently mark the run FAILED.
                try:
                    spec_doc = json.loads(artifact.raw_content)
                except json.JSONDecodeError as exc:
                    raise OpenApiIngestionError(
                        f"Cached OpenAPI artifact is not valid JSON: {exc}"
                    ) from exc
                if not isinstance(spec_doc, dict):
                    raise OpenApiIngestionError(
                        "Cached OpenAPI artifact must be a JSON object."
                    )
                fetch = OpenApiFetchResult(
                    url=fetch.url,
                    final_url=fetch.final_url,
                    status_code=fetch.status_code,
                    content_type=fetch.content_type,
                    raw_body=artifact.raw_content,
                    spec=spec_doc,
                    etag=fetch.etag,
                    last_modified=fetch.last_modified,
                    redirect_chain=fetch.redirect_chain,
                    not_modified=True,
                )
            else:
                captured_at = self._timestamp()
                artifact = _artifact_from_fetch(
                    run_id=run_id, result=fetch, captured_at=captured_at
                )
                self._store.save_ingestion_artifact(artifact)
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

            info = fetch.spec.get("info") or {}
            spec_title = str(info.get("title") or "")[:_SUBJECT_MAX_CHARS]
            spec_version = str(info.get("version") or "")[:128]

            # Generate one claim per operation, plus auxiliaries.
            operations = list(_iter_operations(fetch.spec, spec.max_endpoints_per_spec))
            if not operations:
                raise OpenApiIngestionError(
                    "OpenAPI spec contains no operations to ingest."
                )
            for op in operations:
                operation_count += 1
                for claim in _claims_from_operation(
                    spec=spec,
                    artifact=artifact,
                    fetch=fetch,
                    operation=op,
                    submitted_by=submitted_by,
                    created_at=self._timestamp(),
                ):
                    try:
                        self._store.create(claim)
                    except DuplicateClaimError:
                        # Shouldn't happen with UUID claim ids, but guard anyway.
                        skipped_operations.append(claim.subject)
                        continue
                    created_claim_ids.append(claim.claim_id)
                    if verify:
                        verification = CanonOrchestrator(self._store).verify_claim(claim)
                        saved = self._store.save_verification_result(verification)
                        verification_result_ids.append(saved.verification_id)
        except (
            OpenApiIngestionError,
            DuplicateClaimError,
            DuplicateEvidenceError,
            EvidencePolicyError,
            ToolNotAllowedError,
            ValueError,
        ) as exc:
            errors.append(str(exc))

        completed_at = self._timestamp()
        status = IngestionStatus.COMPLETED if not errors else IngestionStatus.FAILED
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
        return OpenApiIngestionResponse(
            run_id=run_id,
            status=status,
            cache_hit=cache_hit,
            final_url=final_url,
            redirect_chain=list(redirect_chain),
            spec_title=spec_title,
            spec_version=spec_version,
            operation_count=operation_count,
            created_claim_ids=created_claim_ids,
            verification_result_ids=verification_result_ids,
            artifact_ids=artifact_ids,
            errors=errors,
            skipped_operations=skipped_operations,
        )

    def ingest_all_for_tool(
        self,
        *,
        tool_id: str,
        submitted_by: str = "openapi-ingestion-agent",
        verify: bool = True,
    ) -> list[OpenApiIngestionResponse]:
        urls = list_openapi_sources_for_tool(tool_id)
        if not urls:
            raise OpenApiIngestionError(
                f"OpenAPI ingestion is not allowlisted for tool '{tool_id}'."
            )
        return [
            self.ingest(tool_id=tool_id, url=url, submitted_by=submitted_by, verify=verify)
            for url in urls
        ]

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def _fetch(
        self,
        spec: OpenApiFetchSpec,
        cache_entry: DocsFetchCacheEntry | None,
    ) -> OpenApiFetchResult:
        url = spec.url
        redirect_chain: list[str] = []
        for hop in range(spec.max_redirects + 1):
            try:
                assert_url_is_safe(
                    url, allowed_hosts=_official_hosts_for_tool(spec.tool_id)
                )
            except HttpFetchError as exc:
                raise OpenApiIngestionError(str(exc)) from exc

            headers: dict[str, str] = {
                "User-Agent": "AgentAtlas-OpenApiIngestion/1.0",
                "Accept": ", ".join(spec.allowed_content_types),
            }
            if cache_entry is not None and url == spec.url:
                if cache_entry.etag:
                    headers["If-None-Match"] = cache_entry.etag
                if cache_entry.last_modified:
                    headers["If-Modified-Since"] = cache_entry.last_modified

            try:
                response = self._client.get(
                    url, headers=headers, timeout=float(spec.timeout_seconds)
                )
            except httpx.TimeoutException as exc:
                raise OpenApiIngestionError(
                    f"OpenAPI fetch timed out after {spec.timeout_seconds}s."
                ) from exc
            except httpx.RequestError as exc:
                raise OpenApiIngestionError(f"OpenAPI fetch failed: {exc}") from exc

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise OpenApiIngestionError(
                        f"Redirect response {response.status_code} missing Location header."
                    )
                if hop >= spec.max_redirects:
                    raise OpenApiIngestionError(
                        f"OpenAPI fetch exceeded max_redirects={spec.max_redirects}."
                    )
                redirect_chain.append(location)
                url = location
                continue

            if response.status_code == 304:
                return OpenApiFetchResult(
                    url=spec.url,
                    final_url=url,
                    status_code=304,
                    content_type=response.headers.get("content-type", ""),
                    raw_body="",
                    spec={},
                    etag=cache_entry.etag if cache_entry else None,
                    last_modified=cache_entry.last_modified if cache_entry else None,
                    redirect_chain=tuple(redirect_chain),
                    not_modified=True,
                )

            if response.status_code >= 400:
                raise OpenApiIngestionError(
                    f"OpenAPI fetch returned HTTP {response.status_code} for {url}."
                )

            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and content_type not in spec.allowed_content_types:
                raise OpenApiIngestionError(
                    f"OpenAPI fetch returned disallowed content-type {content_type!r}."
                )

            raw_body = response.text
            if len(raw_body.encode("utf-8")) > spec.max_bytes:
                raise OpenApiIngestionError(
                    f"OpenAPI fetch exceeded max_bytes={spec.max_bytes}."
                )
            if not raw_body.strip():
                raise OpenApiIngestionError("OpenAPI fetch returned empty body.")

            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                raise OpenApiIngestionError(
                    f"OpenAPI fetch body is not valid JSON: {exc}"
                ) from exc

            if not isinstance(parsed, dict):
                raise OpenApiIngestionError(
                    "OpenAPI spec must be a JSON object at the root."
                )

            openapi_version = parsed.get("openapi")
            if not isinstance(openapi_version, str) or not openapi_version.startswith("3."):
                raise OpenApiIngestionError(
                    f"Only OpenAPI 3.x is supported; got openapi={openapi_version!r}."
                )

            try:
                openapi_validate(parsed)
            except OpenAPIValidationError as exc:
                raise OpenApiIngestionError(
                    f"OpenAPI spec failed schema validation: {exc}"
                ) from exc

            return OpenApiFetchResult(
                url=spec.url,
                final_url=url,
                status_code=response.status_code,
                content_type=content_type,
                raw_body=raw_body,
                spec=parsed,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                redirect_chain=tuple(redirect_chain),
                not_modified=False,
            )

        raise OpenApiIngestionError(
            f"OpenAPI fetch exhausted redirects without a final response for {spec.url}."
        )


# -------- Contract loading and lookup --------


def openapi_fetch_spec(*, tool_id: str, url: str) -> OpenApiFetchSpec:
    normalized_tool_id = tool_id.strip().casefold()
    data = _openapi_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        raise OpenApiIngestionError(
            f"OpenAPI ingestion is not allowlisted for tool '{tool_id}'."
        )
    for item in tool["sources"]:
        if item["url"] != url:
            continue
        return OpenApiFetchSpec(
            tool_id=normalized_tool_id,
            url=item["url"],
            evidence_type=EvidenceType(item["evidence_type"]),
            timeout_seconds=int(data["default_timeout_seconds"]),
            max_bytes=int(data["default_max_bytes"]),
            max_redirects=int(data["max_redirects"]),
            max_endpoints_per_spec=int(data["max_endpoints_per_spec"]),
            allowed_content_types=tuple(data["allowed_content_types"]),
        )
    raise OpenApiIngestionError(
        f"URL '{url}' is not allowlisted for OpenAPI ingestion of tool '{tool_id}'."
    )


def list_openapi_sources_for_tool(tool_id: str) -> list[str]:
    normalized_tool_id = tool_id.strip().casefold()
    data = _openapi_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        return []
    return [str(item["url"]) for item in tool["sources"]]


@cache
def _openapi_ingestion_sources() -> dict[str, Any]:
    with _OPENAPI_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_openapi_ingestion_sources(data)
    return data


def _validate_openapi_ingestion_sources(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("OpenAPI ingestion source contract must have version=1")
    required = (
        "default_timeout_seconds",
        "default_max_bytes",
        "default_cache_ttl_seconds",
        "max_redirects",
        "max_endpoints_per_spec",
        "allowed_content_types",
        "tools",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"OpenAPI ingestion source contract missing {key}")
    for key in ("default_timeout_seconds", "default_max_bytes", "max_endpoints_per_spec"):
        if not isinstance(data[key], int) or data[key] <= 0:
            raise ValueError(f"{key} must be a positive int")
    if not isinstance(data["max_redirects"], int) or data["max_redirects"] < 0:
        raise ValueError("max_redirects must be a non-negative int")
    if not isinstance(data["allowed_content_types"], list) or not data["allowed_content_types"]:
        raise ValueError("allowed_content_types must be a non-empty list")

    trust = _trust_sources()
    tools = data["tools"]
    if not isinstance(tools, dict) or not tools:
        raise ValueError("tools must be a non-empty mapping")
    for tool_id, tool in tools.items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("invalid tool id in OpenAPI ingestion sources")
        if tool_id not in trust["tools"]:
            raise ValueError(
                f"OpenAPI ingestion tool '{tool_id}' is not in tool_trust_sources contract"
            )
        official_hosts = set(trust["tools"][tool_id]["official_hosts"])
        sources = tool.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"tool '{tool_id}' must define at least one source")
        for source in sources:
            for key in ("url", "evidence_type"):
                if key not in source:
                    raise ValueError(f"tool '{tool_id}' source missing {key}")
            from urllib.parse import urlparse

            url = source["url"]
            parsed = urlparse(url)
            if parsed.scheme != "https":
                raise ValueError(f"tool '{tool_id}' source url must be https")
            hostname = (parsed.hostname or "").lower()
            if not host_in_allowlist(hostname, official_hosts):
                raise ValueError(
                    f"tool '{tool_id}' source host {hostname!r} is not in official_hosts"
                )
            EvidenceType(source["evidence_type"])


def _official_hosts_for_tool(tool_id: str) -> frozenset[str]:
    trust = _trust_sources()
    tool = trust["tools"].get(tool_id)
    if not tool:
        return frozenset()
    return frozenset(tool["official_hosts"])


# -------- Operation iteration + claim generation --------


@dataclass(frozen=True)
class _OpenApiOperation:
    method: str  # lowercase
    path: str
    operation: dict[str, Any]
    spec_security: list[dict[str, Any]]
    json_pointer: str  # without the leading '#'


def _iter_operations(
    spec: dict[str, Any], max_endpoints: int
) -> list[_OpenApiOperation]:
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return []
    spec_security = spec.get("security") or []
    if not isinstance(spec_security, list):
        spec_security = []

    out: list[_OpenApiOperation] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method in sorted(path_item.keys()):
            method_lower = method.lower()
            if method_lower not in _OPENAPI_METHODS:
                continue
            operation = path_item[method]
            if not isinstance(operation, dict):
                continue
            out.append(
                _OpenApiOperation(
                    method=method_lower,
                    path=path,
                    operation=operation,
                    spec_security=list(spec_security),
                    json_pointer=f"/paths/{_escape_json_pointer(path)}/{method_lower}",
                )
            )
            if len(out) >= max_endpoints:
                return out
    return out


def _escape_json_pointer(value: str) -> str:
    """RFC 6901 escaping: ~ -> ~0 and / -> ~1."""
    return value.replace("~", "~0").replace("/", "~1")


def _claims_from_operation(
    *,
    spec: OpenApiFetchSpec,
    artifact: RawIngestionArtifact,
    fetch: OpenApiFetchResult,
    operation: _OpenApiOperation,
    submitted_by: str,
    created_at: datetime,
) -> list[KnowledgeClaim]:
    method = operation.method
    path = operation.path
    op_security = operation.operation.get("security")
    effective_security = (
        op_security if isinstance(op_security, list) else operation.spec_security
    )

    # Each claim references a distinct JSON Pointer suffix so audits can find
    # exactly which node grounded each assertion.
    claims: list[KnowledgeClaim] = []

    subject_base = f"{method.upper()} {path}"[:_SUBJECT_MAX_CHARS]
    summary = (
        operation.operation.get("summary")
        or operation.operation.get("description")
        or f"{method.upper()} {path}"
    )
    statement = " ".join(str(summary).split())[:_STATEMENT_MAX_CHARS] or subject_base

    # Risk: method drives base risk; auth-required + cost-incurring upgrades happen elsewhere.
    if method in _DESTRUCTIVE_METHODS:
        risk = RiskLevel.CRITICAL
    elif method in _MUTATING_METHODS:
        risk = RiskLevel.HIGH
    else:
        risk = RiskLevel.LOW

    # Primary endpoint-existence claim.
    claims.append(
        _build_claim(
            spec=spec,
            artifact=artifact,
            fetch=fetch,
            claim_type=ClaimType.API_ENDPOINT_EXISTS,
            subject=subject_base,
            statement=statement,
            risk_level=risk,
            pointer=operation.json_pointer,
            submitted_by=submitted_by,
            created_at=created_at,
        )
    )

    if effective_security:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.AUTH_REQUIREMENT,
                subject=f"{subject_base} (auth)"[:_SUBJECT_MAX_CHARS],
                statement=f"Endpoint {subject_base} requires authentication per spec security block.",
                risk_level=RiskLevel.MEDIUM,
                pointer=(
                    f"{operation.json_pointer}/security"
                    if op_security is not None
                    else "/security"
                ),
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    if method in _MUTATING_METHODS:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.SIDE_EFFECT,
                subject=f"{subject_base} (side effect)"[:_SUBJECT_MAX_CHARS],
                statement=f"{method.upper()} {path} mutates remote state.",
                risk_level=RiskLevel.HIGH,
                pointer=operation.json_pointer,
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    if method in _DESTRUCTIVE_METHODS:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.DESTRUCTIVE_ACTION,
                subject=f"{subject_base} (destructive)"[:_SUBJECT_MAX_CHARS],
                statement=f"DELETE {path} is a destructive remote mutation.",
                risk_level=RiskLevel.CRITICAL,
                pointer=operation.json_pointer,
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    if operation.operation.get("deprecated") is True:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.FEATURE_DEPRECATED,
                subject=f"{subject_base} (deprecated)"[:_SUBJECT_MAX_CHARS],
                statement=f"{method.upper()} {path} is marked deprecated in the spec.",
                risk_level=RiskLevel.MEDIUM,
                pointer=f"{operation.json_pointer}/deprecated",
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    return claims


def _build_claim(
    *,
    spec: OpenApiFetchSpec,
    artifact: RawIngestionArtifact,
    fetch: OpenApiFetchResult,
    claim_type: ClaimType,
    subject: str,
    statement: str,
    risk_level: RiskLevel,
    pointer: str,
    submitted_by: str,
    created_at: datetime,
) -> KnowledgeClaim:
    # Evidence.source_uri = spec URL + RFC 6901 JSON Pointer fragment so the
    # evidence record points at the exact node that grounded this claim.
    source_uri = f"{fetch.final_url}#{pointer}"
    excerpt = (statement or subject)[:8000]
    evidence = Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=spec.evidence_type,
        source_uri=source_uri,
        excerpt=excerpt,
        hash=artifact.hash,
        captured_at=artifact.captured_at,
        trust_level=TrustLevel.HIGH,
    )
    return KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=claim_type,
        subject=subject,
        statement=statement,
        tool_id=spec.tool_id,
        submitted_by=submitted_by,
        evidence=[evidence],
        risk_level=risk_level,
        created_at=created_at,
    )


def _artifact_from_fetch(
    *,
    run_id: str,
    result: OpenApiFetchResult,
    captured_at: datetime,
) -> RawIngestionArtifact:
    artifact_id = generate_ingestion_artifact_id()
    source_uri = f"agentatlas://ingestion/{run_id}/artifacts/{artifact_id}"
    return RawIngestionArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=IngestionArtifactType.OPENAPI_SPEC,
        source_uri=source_uri,
        raw_content=result.raw_body,
        hash=f"sha256:{sha256(result.raw_body.encode('utf-8')).hexdigest()}",
        captured_at=captured_at,
    )


__all__ = [
    "HttpxOpenApiClient",
    "OpenApiFetchResult",
    "OpenApiFetchSpec",
    "OpenApiHttpClient",
    "OpenApiIngestionError",
    "OpenApiIngestionService",
    "list_openapi_sources_for_tool",
    "openapi_fetch_spec",
]
