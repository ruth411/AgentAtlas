"""Stage 7c.3: Safe GraphQL schema (SDL) ingestion.

This module fetches GraphQL Schema Definition Language (SDL) documents from
per-tool allowlisted URLs, parses + validates them via `graphql-core`'s
`build_schema`, and emits a structured claim per root operation field
(Query / Mutation / Subscription).

Per root field we emit:

- `api_endpoint_exists`   (primary)   — `<OperationType>.<fieldName>` exists.
- `side_effect`           (auxiliary) — any Mutation field.
- `destructive_action`    (auxiliary) — Mutation field whose name starts with
                                        a destructive prefix from the contract
                                        (`delete`, `remove`, `destroy`, etc.).
- `feature_deprecated`    (auxiliary) — field with `@deprecated` directive
                                        (graphql-core surfaces this as
                                        `deprecation_reason`).

Provenance: GraphQL SDL has no JSON Pointer, but root field locations are
unique within their parent operation type. The evidence `source_uri` carries
a fragment `#<OperationType>.<fieldName>` so an auditor can trace any
canonical assertion back to the exact SDL location.

The trust boundary mirrors Stage 7c.1 / 7c.2: contract gate, shared SSRF
guard, manual redirect re-validation, content-type allowlist, hard
`max_bytes` and `timeout_seconds`, and conditional revalidation via the
shared `docs_fetch_cache` table. SDL is fetched only from per-tool
`official_hosts` (no aggregator block for GraphQL — SDL files are typically
hosted by the API vendor itself).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Protocol

import httpx
from graphql import GraphQLError, build_schema
from graphql.type import GraphQLField

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
    GraphqlIngestionResponse,
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
from app.services.evidence_trust import _trust_sources
from app.services.http_safety import (
    HttpFetchError,
    assert_url_is_safe,
)
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_ingestion_artifact_id,
    generate_ingestion_run_id,
)
from app.services.contract_paths import contract_path
from app.services.orchestrator import CanonOrchestrator


_GRAPHQL_CONTRACT = contract_path("graphql_ingestion_sources.v1.json")

_STATEMENT_MAX_CHARS = 480
_SUBJECT_MAX_CHARS = 512


class GraphqlIngestionError(ValueError):
    """Raised when a GraphQL ingestion request is unsupported, unsafe, or fails."""


# -------- Specs --------


@dataclass(frozen=True)
class GraphqlFetchSpec:
    tool_id: str
    url: str
    evidence_type: EvidenceType
    subject_prefix: str
    timeout_seconds: int
    max_bytes: int
    max_redirects: int
    max_root_fields_per_schema: int
    destructive_field_name_prefixes: tuple[str, ...]
    allowed_content_types: tuple[str, ...]


@dataclass(frozen=True)
class GraphqlFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    raw_body: str
    etag: str | None
    last_modified: str | None
    redirect_chain: tuple[str, ...]
    not_modified: bool


# -------- HTTP client --------


class GraphqlHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        """Issue a single GET. Must NOT follow redirects automatically."""


class HttpxGraphqlClient:
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


class GraphqlIngestionService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        client: GraphqlHttpClient | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        self._client = client or HttpxGraphqlClient()
        self._now = now

    def ingest(
        self,
        *,
        tool_id: str,
        url: str,
        submitted_by: str = "graphql-ingestion-agent",
        verify: bool = True,
    ) -> GraphqlIngestionResponse:
        spec = graphql_fetch_spec(tool_id=tool_id, url=url)
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
        query_field_count = 0
        mutation_field_count = 0
        subscription_field_count = 0
        skipped_fields: list[str] = []

        try:
            cache_entry = self._store.get_docs_fetch_cache(spec.url)
            fetch = self._fetch(spec, cache_entry)
            final_url = fetch.final_url
            redirect_chain = fetch.redirect_chain
            cache_hit = fetch.not_modified

            if fetch.not_modified and cache_entry is not None and cache_entry.last_artifact_id:
                artifact = self._store.get_ingestion_artifact(cache_entry.last_artifact_id)
                if artifact is None:
                    raise GraphqlIngestionError(
                        "Cache hit but the prior artifact has been deleted; refetch required."
                    )
                # On a 304 we don't re-parse — we trust the cached artifact's
                # raw_content for the SDL body.
                sdl_body = artifact.raw_content
                artifact_ids.append(artifact.artifact_id)
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
                sdl_body = fetch.raw_body

            # Parse + validate SDL. build_schema raises GraphQLError on any
            # syntactic or type-system invariant violation.
            try:
                schema = build_schema(sdl_body)
            except GraphQLError as exc:
                raise GraphqlIngestionError(
                    f"GraphQL SDL failed parse / type-system validation: {exc.message}"
                ) from exc
            except Exception as exc:  # graphql-core occasionally raises non-GraphQLError
                raise GraphqlIngestionError(
                    f"GraphQL SDL could not be parsed: {exc}"
                ) from exc

            root_groups: list[tuple[str, dict[str, GraphQLField]]] = []
            if schema.query_type is not None:
                root_groups.append(("Query", dict(schema.query_type.fields)))
            if schema.mutation_type is not None:
                root_groups.append(("Mutation", dict(schema.mutation_type.fields)))
            if schema.subscription_type is not None:
                root_groups.append(("Subscription", dict(schema.subscription_type.fields)))

            total_root_fields = sum(len(fields) for _, fields in root_groups)
            if total_root_fields == 0:
                raise GraphqlIngestionError(
                    "GraphQL schema declares no Query / Mutation / Subscription root fields."
                )

            emitted = 0
            for operation_type, fields in root_groups:
                for field_name in sorted(fields.keys()):
                    if emitted >= spec.max_root_fields_per_schema:
                        skipped_fields.append(f"{operation_type}.{field_name}")
                        continue
                    field = fields[field_name]
                    if operation_type == "Query":
                        query_field_count += 1
                    elif operation_type == "Mutation":
                        mutation_field_count += 1
                    else:
                        subscription_field_count += 1
                    emitted += 1

                    for claim in _claims_from_field(
                        spec=spec,
                        artifact=artifact,
                        fetch=fetch,
                        operation_type=operation_type,
                        field_name=field_name,
                        field=field,
                        submitted_by=submitted_by,
                        created_at=self._timestamp(),
                    ):
                        try:
                            self._store.create(claim)
                        except DuplicateClaimError:
                            skipped_fields.append(claim.subject)
                            continue
                        created_claim_ids.append(claim.claim_id)
                        if verify:
                            verification = CanonOrchestrator(self._store).verify_claim(claim)
                            saved = self._store.save_verification_result(verification)
                            verification_result_ids.append(saved.verification_id)
        except (
            GraphqlIngestionError,
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
        return GraphqlIngestionResponse(
            run_id=run_id,
            status=status,
            cache_hit=cache_hit,
            final_url=final_url,
            redirect_chain=list(redirect_chain),
            query_field_count=query_field_count,
            mutation_field_count=mutation_field_count,
            subscription_field_count=subscription_field_count,
            created_claim_ids=created_claim_ids,
            verification_result_ids=verification_result_ids,
            artifact_ids=artifact_ids,
            errors=errors,
            skipped_fields=skipped_fields,
        )

    def ingest_all_for_tool(
        self,
        *,
        tool_id: str,
        submitted_by: str = "graphql-ingestion-agent",
        verify: bool = True,
    ) -> list[GraphqlIngestionResponse]:
        urls = list_graphql_sources_for_tool(tool_id)
        if not urls:
            raise GraphqlIngestionError(
                f"GraphQL ingestion is not allowlisted for tool '{tool_id}'."
            )
        return [
            self.ingest(tool_id=tool_id, url=url, submitted_by=submitted_by, verify=verify)
            for url in urls
        ]

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def _fetch(
        self,
        spec: GraphqlFetchSpec,
        cache_entry: DocsFetchCacheEntry | None,
    ) -> GraphqlFetchResult:
        url = spec.url
        redirect_chain: list[str] = []
        allowed_hosts = _official_hosts_for_tool(spec.tool_id)
        for hop in range(spec.max_redirects + 1):
            try:
                assert_url_is_safe(url, allowed_hosts=allowed_hosts)
            except HttpFetchError as exc:
                raise GraphqlIngestionError(str(exc)) from exc

            headers: dict[str, str] = {
                "User-Agent": "AgentAtlas-GraphqlIngestion/1.0",
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
                raise GraphqlIngestionError(
                    f"GraphQL SDL fetch timed out after {spec.timeout_seconds}s."
                ) from exc
            except httpx.RequestError as exc:
                raise GraphqlIngestionError(
                    f"GraphQL SDL fetch failed: {exc}"
                ) from exc

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise GraphqlIngestionError(
                        f"Redirect response {response.status_code} missing Location header."
                    )
                if hop >= spec.max_redirects:
                    raise GraphqlIngestionError(
                        f"GraphQL SDL fetch exceeded max_redirects={spec.max_redirects}."
                    )
                redirect_chain.append(location)
                url = location
                continue

            if response.status_code == 304:
                return GraphqlFetchResult(
                    url=spec.url,
                    final_url=url,
                    status_code=304,
                    content_type=response.headers.get("content-type", ""),
                    raw_body="",
                    etag=cache_entry.etag if cache_entry else None,
                    last_modified=cache_entry.last_modified if cache_entry else None,
                    redirect_chain=tuple(redirect_chain),
                    not_modified=True,
                )

            if response.status_code >= 400:
                raise GraphqlIngestionError(
                    f"GraphQL SDL fetch returned HTTP {response.status_code} for {url}."
                )

            content_type = (
                response.headers.get("content-type", "").split(";")[0].strip().lower()
            )
            if content_type and content_type not in spec.allowed_content_types:
                raise GraphqlIngestionError(
                    f"GraphQL SDL fetch returned disallowed content-type {content_type!r}."
                )

            raw_body = response.text
            if len(raw_body.encode("utf-8")) > spec.max_bytes:
                raise GraphqlIngestionError(
                    f"GraphQL SDL fetch exceeded max_bytes={spec.max_bytes}."
                )
            if not raw_body.strip():
                raise GraphqlIngestionError("GraphQL SDL fetch returned empty body.")

            return GraphqlFetchResult(
                url=spec.url,
                final_url=url,
                status_code=response.status_code,
                content_type=content_type,
                raw_body=raw_body,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                redirect_chain=tuple(redirect_chain),
                not_modified=False,
            )

        raise GraphqlIngestionError(
            f"GraphQL SDL fetch exhausted redirects without a final response for {spec.url}."
        )


# -------- Contract loading and lookup --------


def graphql_fetch_spec(*, tool_id: str, url: str) -> GraphqlFetchSpec:
    normalized_tool_id = tool_id.strip().casefold()
    data = _graphql_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        raise GraphqlIngestionError(
            f"GraphQL ingestion is not allowlisted for tool '{tool_id}'."
        )
    for item in tool["sources"]:
        if item["url"] != url:
            continue
        return GraphqlFetchSpec(
            tool_id=normalized_tool_id,
            url=item["url"],
            evidence_type=EvidenceType(item["evidence_type"]),
            subject_prefix=str(item.get("subject_prefix") or ""),
            timeout_seconds=int(data["default_timeout_seconds"]),
            max_bytes=int(data["default_max_bytes"]),
            max_redirects=int(data["max_redirects"]),
            max_root_fields_per_schema=int(data["max_root_fields_per_schema"]),
            destructive_field_name_prefixes=tuple(
                str(p).lower() for p in data["destructive_field_name_prefixes"]
            ),
            allowed_content_types=tuple(data["allowed_content_types"]),
        )
    raise GraphqlIngestionError(
        f"URL '{url}' is not allowlisted for GraphQL ingestion of tool '{tool_id}'."
    )


def list_graphql_sources_for_tool(tool_id: str) -> list[str]:
    normalized_tool_id = tool_id.strip().casefold()
    data = _graphql_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        return []
    return [str(item["url"]) for item in tool["sources"]]


@cache
def _graphql_ingestion_sources() -> dict[str, Any]:
    with _GRAPHQL_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_graphql_ingestion_sources(data)
    return data


def _validate_graphql_ingestion_sources(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("GraphQL ingestion source contract must have version=1")
    required = (
        "default_timeout_seconds",
        "default_max_bytes",
        "default_cache_ttl_seconds",
        "max_redirects",
        "max_root_fields_per_schema",
        "destructive_field_name_prefixes",
        "allowed_content_types",
        "tools",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"GraphQL ingestion source contract missing {key}")
    for key in (
        "default_timeout_seconds",
        "default_max_bytes",
        "max_root_fields_per_schema",
    ):
        if not isinstance(data[key], int) or data[key] <= 0:
            raise ValueError(f"{key} must be a positive int")
    if not isinstance(data["max_redirects"], int) or data["max_redirects"] < 0:
        raise ValueError("max_redirects must be a non-negative int")
    if (
        not isinstance(data["allowed_content_types"], list)
        or not data["allowed_content_types"]
    ):
        raise ValueError("allowed_content_types must be a non-empty list")
    if (
        not isinstance(data["destructive_field_name_prefixes"], list)
        or not data["destructive_field_name_prefixes"]
    ):
        raise ValueError(
            "destructive_field_name_prefixes must be a non-empty list"
        )
    for p in data["destructive_field_name_prefixes"]:
        if not isinstance(p, str) or not p:
            raise ValueError("destructive_field_name_prefixes entries must be non-empty strings")

    trust = _trust_sources()
    tools = data["tools"]
    if not isinstance(tools, dict) or not tools:
        raise ValueError("tools must be a non-empty mapping")
    for tool_id, tool in tools.items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("invalid tool id in GraphQL ingestion sources")
        if tool_id not in trust["tools"]:
            raise ValueError(
                f"GraphQL ingestion tool '{tool_id}' is not in tool_trust_sources contract"
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
            from app.services.http_safety import host_in_allowlist

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


# -------- Claim generation --------


def _is_destructive_field(
    field_name: str, prefixes: tuple[str, ...]
) -> bool:
    lower = field_name.lower()
    return any(lower.startswith(p) for p in prefixes)


def _claims_from_field(
    *,
    spec: GraphqlFetchSpec,
    artifact: RawIngestionArtifact,
    fetch: GraphqlFetchResult,
    operation_type: str,  # "Query" | "Mutation" | "Subscription"
    field_name: str,
    field: GraphQLField,
    submitted_by: str,
    created_at: datetime,
) -> list[KnowledgeClaim]:
    prefix = spec.subject_prefix or spec.tool_id
    subject_base = f"{prefix} {operation_type}: {field_name}"[:_SUBJECT_MAX_CHARS]
    description = field.description or ""
    return_type = str(field.type)
    statement = (
        f"{prefix} exposes GraphQL {operation_type.lower()} field "
        f"`{field_name}` returning {return_type}."
    )
    if description:
        statement = f"{statement} {' '.join(str(description).split())}"
    statement = statement[:_STATEMENT_MAX_CHARS]

    # Risk: queries / subscriptions are read-side; mutations always HIGH;
    # destructive mutations CRITICAL.
    is_mutation = operation_type == "Mutation"
    is_destructive = is_mutation and _is_destructive_field(
        field_name, spec.destructive_field_name_prefixes
    )
    if is_destructive:
        primary_risk = RiskLevel.CRITICAL
    elif is_mutation:
        primary_risk = RiskLevel.HIGH
    else:
        primary_risk = RiskLevel.LOW

    pointer = f"{operation_type}.{field_name}"
    claims: list[KnowledgeClaim] = [
        _build_claim(
            spec=spec,
            artifact=artifact,
            fetch=fetch,
            claim_type=ClaimType.API_ENDPOINT_EXISTS,
            subject=subject_base,
            statement=statement,
            risk_level=primary_risk,
            pointer=pointer,
            submitted_by=submitted_by,
            created_at=created_at,
        )
    ]

    if is_mutation:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.SIDE_EFFECT,
                subject=f"{subject_base} (side effect)"[:_SUBJECT_MAX_CHARS],
                statement=(
                    f"GraphQL mutation `{field_name}` mutates remote state on {prefix}."
                )[:_STATEMENT_MAX_CHARS],
                risk_level=RiskLevel.HIGH,
                pointer=pointer,
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    if is_destructive:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.DESTRUCTIVE_ACTION,
                subject=f"{subject_base} (destructive)"[:_SUBJECT_MAX_CHARS],
                statement=(
                    f"GraphQL mutation `{field_name}` is a destructive remote "
                    f"action on {prefix} (matched destructive prefix)."
                )[:_STATEMENT_MAX_CHARS],
                risk_level=RiskLevel.CRITICAL,
                pointer=pointer,
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    if field.deprecation_reason:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.FEATURE_DEPRECATED,
                subject=f"{subject_base} (deprecated)"[:_SUBJECT_MAX_CHARS],
                statement=(
                    f"GraphQL {operation_type.lower()} field `{field_name}` is "
                    f"deprecated: { ' '.join(str(field.deprecation_reason).split()) }"
                )[:_STATEMENT_MAX_CHARS],
                risk_level=RiskLevel.MEDIUM,
                pointer=f"{pointer}@deprecated",
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    return claims


def _build_claim(
    *,
    spec: GraphqlFetchSpec,
    artifact: RawIngestionArtifact,
    fetch: GraphqlFetchResult,
    claim_type: ClaimType,
    subject: str,
    statement: str,
    risk_level: RiskLevel,
    pointer: str,
    submitted_by: str,
    created_at: datetime,
) -> KnowledgeClaim:
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
    result: GraphqlFetchResult,
    captured_at: datetime,
) -> RawIngestionArtifact:
    artifact_id = generate_ingestion_artifact_id()
    source_uri = f"agentatlas://ingestion/{run_id}/artifacts/{artifact_id}"
    return RawIngestionArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=IngestionArtifactType.GRAPHQL_SDL,
        source_uri=source_uri,
        raw_content=result.raw_body,
        hash=f"sha256:{sha256(result.raw_body.encode('utf-8')).hexdigest()}",
        captured_at=captured_at,
    )


__all__ = [
    "GraphqlFetchResult",
    "GraphqlFetchSpec",
    "GraphqlHttpClient",
    "GraphqlIngestionError",
    "GraphqlIngestionService",
    "HttpxGraphqlClient",
    "graphql_fetch_spec",
    "list_graphql_sources_for_tool",
]
