"""Stage 7c.2: Safe JSON Schema ingestion.

This module fetches JSON Schema documents (Draft 4 / 6 / 7 / 2019-09 / 2020-12)
from per-tool allowlisted URLs, validates each document against the dialect
declared by its own `$schema` keyword, and emits a structured claim per
top-level property in the schema's `properties` map.

Per top-level property we emit:

- `config_field_exists` (primary)   — `<tool>` accepts a config field with
                                       this name in `<subject_prefix>`.
- `feature_deprecated`  (auxiliary) — when the property carries
                                       `deprecated: true` (Draft 2019-09+).

Every claim's evidence `source_uri` includes a JSON Pointer (RFC 6901) to
the exact node inside the schema that grounded the claim, so an auditor can
trace any canonical assertion back to the byte that produced it.

The trust boundary mirrors Stage 7c.1: contract gate (URL must be in
`contracts/json_schema_ingestion_sources.v1.json`), shared SSRF guard with
host allowlist drawn from the per-tool `official_hosts` *unioned* with the
top-level `schema_aggregator_hosts.json_schema` from
`tool_trust_sources.v1.json`, manual redirect re-validation, JSON content-type
allowlist, hard `max_bytes` and `timeout_seconds`, and conditional
revalidation via the existing `docs_fetch_cache` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
import json
from typing import Any, Protocol

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

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
    JsonSchemaIngestionResponse,
    RawIngestionArtifact,
)
from app.services.claim_store import (
    ClaimStore,
    DuplicateClaimError,
    DuplicateEvidenceError,
    EvidencePolicyError,
    ToolNotAllowedError,
)
from app.services.evidence_trust import _trust_sources, schema_aggregator_hosts
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


_JSON_SCHEMA_CONTRACT = contract_path("json_schema_ingestion_sources.v1.json")

_STATEMENT_MAX_CHARS = 480
_SUBJECT_MAX_CHARS = 512


class JsonSchemaIngestionError(ValueError):
    """Raised when a JSON Schema ingestion request is unsupported, unsafe, or fails."""


# -------- Specs --------


@dataclass(frozen=True)
class JsonSchemaFetchSpec:
    tool_id: str
    url: str
    evidence_type: EvidenceType
    subject_prefix: str
    timeout_seconds: int
    max_bytes: int
    max_redirects: int
    max_fields_per_schema: int
    allowed_content_types: tuple[str, ...]


@dataclass(frozen=True)
class JsonSchemaFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    raw_body: str
    document: dict[str, Any]
    etag: str | None
    last_modified: str | None
    redirect_chain: tuple[str, ...]
    not_modified: bool


# -------- HTTP client --------


class JsonSchemaHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        """Issue a single GET. Must NOT follow redirects automatically."""


class HttpxJsonSchemaClient:
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


class JsonSchemaIngestionService:
    def __init__(
        self,
        store: ClaimStore,
        *,
        client: JsonSchemaHttpClient | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        self._client = client or HttpxJsonSchemaClient()
        self._now = now

    def ingest(
        self,
        *,
        tool_id: str,
        url: str,
        submitted_by: str = "json-schema-ingestion-agent",
        verify: bool = True,
    ) -> JsonSchemaIngestionResponse:
        spec = json_schema_fetch_spec(tool_id=tool_id, url=url)
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
        schema_id = ""
        schema_title = ""
        schema_dialect = ""
        field_count = 0
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
                    raise JsonSchemaIngestionError(
                        "Cache hit but the prior artifact has been deleted; refetch required."
                    )
                artifact_ids.append(artifact.artifact_id)
                # 304 means the server confirmed the schema body hasn't changed;
                # re-parse the cached body so we can emit a fresh claim chain at
                # the current timestamp. Without this, `fetch.document` is empty
                # and the "no top-level properties" guard silently fails the run.
                try:
                    document_doc = json.loads(artifact.raw_content)
                except json.JSONDecodeError as exc:
                    raise JsonSchemaIngestionError(
                        f"Cached JSON Schema artifact is not valid JSON: {exc}"
                    ) from exc
                if not isinstance(document_doc, dict):
                    raise JsonSchemaIngestionError(
                        "Cached JSON Schema artifact must be a JSON object."
                    )
                fetch = JsonSchemaFetchResult(
                    url=fetch.url,
                    final_url=fetch.final_url,
                    status_code=fetch.status_code,
                    content_type=fetch.content_type,
                    raw_body=artifact.raw_content,
                    document=document_doc,
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

            schema_id = str(fetch.document.get("$id") or "")[:512]
            schema_title = str(fetch.document.get("title") or "")[:_SUBJECT_MAX_CHARS]
            schema_dialect = str(fetch.document.get("$schema") or "")[:256]

            fields = list(_iter_top_level_fields(fetch.document, spec.max_fields_per_schema))
            if not fields:
                raise JsonSchemaIngestionError(
                    "JSON Schema declares no top-level properties to ingest."
                )
            required = _required_set(fetch.document)
            for field in fields:
                field_count += 1
                for claim in _claims_from_field(
                    spec=spec,
                    artifact=artifact,
                    fetch=fetch,
                    field=field,
                    is_required=field.name in required,
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
            JsonSchemaIngestionError,
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
        return JsonSchemaIngestionResponse(
            run_id=run_id,
            status=status,
            cache_hit=cache_hit,
            final_url=final_url,
            redirect_chain=list(redirect_chain),
            schema_id=schema_id,
            schema_title=schema_title,
            schema_dialect=schema_dialect,
            field_count=field_count,
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
        submitted_by: str = "json-schema-ingestion-agent",
        verify: bool = True,
    ) -> list[JsonSchemaIngestionResponse]:
        urls = list_json_schema_sources_for_tool(tool_id)
        if not urls:
            raise JsonSchemaIngestionError(
                f"JSON Schema ingestion is not allowlisted for tool '{tool_id}'."
            )
        return [
            self.ingest(tool_id=tool_id, url=url, submitted_by=submitted_by, verify=verify)
            for url in urls
        ]

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def _fetch(
        self,
        spec: JsonSchemaFetchSpec,
        cache_entry: DocsFetchCacheEntry | None,
    ) -> JsonSchemaFetchResult:
        url = spec.url
        redirect_chain: list[str] = []
        allowed_hosts = _allowed_hosts_for_tool(spec.tool_id)
        for hop in range(spec.max_redirects + 1):
            try:
                assert_url_is_safe(url, allowed_hosts=allowed_hosts)
            except HttpFetchError as exc:
                raise JsonSchemaIngestionError(str(exc)) from exc

            headers: dict[str, str] = {
                "User-Agent": "Ayiru-JsonSchemaIngestion/1.0",
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
                raise JsonSchemaIngestionError(
                    f"JSON Schema fetch timed out after {spec.timeout_seconds}s."
                ) from exc
            except httpx.RequestError as exc:
                raise JsonSchemaIngestionError(
                    f"JSON Schema fetch failed: {exc}"
                ) from exc

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise JsonSchemaIngestionError(
                        f"Redirect response {response.status_code} missing Location header."
                    )
                if hop >= spec.max_redirects:
                    raise JsonSchemaIngestionError(
                        f"JSON Schema fetch exceeded max_redirects={spec.max_redirects}."
                    )
                redirect_chain.append(location)
                url = location
                continue

            if response.status_code == 304:
                return JsonSchemaFetchResult(
                    url=spec.url,
                    final_url=url,
                    status_code=304,
                    content_type=response.headers.get("content-type", ""),
                    raw_body="",
                    document={},
                    etag=cache_entry.etag if cache_entry else None,
                    last_modified=cache_entry.last_modified if cache_entry else None,
                    redirect_chain=tuple(redirect_chain),
                    not_modified=True,
                )

            if response.status_code >= 400:
                raise JsonSchemaIngestionError(
                    f"JSON Schema fetch returned HTTP {response.status_code} for {url}."
                )

            content_type = (
                response.headers.get("content-type", "").split(";")[0].strip().lower()
            )
            if content_type and content_type not in spec.allowed_content_types:
                raise JsonSchemaIngestionError(
                    f"JSON Schema fetch returned disallowed content-type {content_type!r}."
                )

            raw_body = response.text
            if len(raw_body.encode("utf-8")) > spec.max_bytes:
                raise JsonSchemaIngestionError(
                    f"JSON Schema fetch exceeded max_bytes={spec.max_bytes}."
                )
            if not raw_body.strip():
                raise JsonSchemaIngestionError("JSON Schema fetch returned empty body.")

            try:
                document = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                raise JsonSchemaIngestionError(
                    f"JSON Schema body is not valid JSON: {exc}"
                ) from exc

            if not isinstance(document, dict):
                raise JsonSchemaIngestionError(
                    "JSON Schema document must be a JSON object at the root."
                )

            # Validate the document against its declared dialect (or default
            # to Draft 2020-12 if $schema is missing).
            ValidatorCls = (
                validator_for(document) if document.get("$schema") else Draft202012Validator
            )
            try:
                ValidatorCls.check_schema(document)
            except SchemaError as exc:
                raise JsonSchemaIngestionError(
                    f"JSON Schema failed meta-schema validation: {exc.message}"
                ) from exc

            return JsonSchemaFetchResult(
                url=spec.url,
                final_url=url,
                status_code=response.status_code,
                content_type=content_type,
                raw_body=raw_body,
                document=document,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                redirect_chain=tuple(redirect_chain),
                not_modified=False,
            )

        raise JsonSchemaIngestionError(
            f"JSON Schema fetch exhausted redirects without a final response for {spec.url}."
        )


# -------- Contract loading and lookup --------


def json_schema_fetch_spec(*, tool_id: str, url: str) -> JsonSchemaFetchSpec:
    normalized_tool_id = tool_id.strip().casefold()
    data = _json_schema_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        raise JsonSchemaIngestionError(
            f"JSON Schema ingestion is not allowlisted for tool '{tool_id}'."
        )
    for item in tool["sources"]:
        if item["url"] != url:
            continue
        return JsonSchemaFetchSpec(
            tool_id=normalized_tool_id,
            url=item["url"],
            evidence_type=EvidenceType(item["evidence_type"]),
            subject_prefix=str(item.get("subject_prefix") or ""),
            timeout_seconds=int(data["default_timeout_seconds"]),
            max_bytes=int(data["default_max_bytes"]),
            max_redirects=int(data["max_redirects"]),
            max_fields_per_schema=int(data["max_fields_per_schema"]),
            allowed_content_types=tuple(data["allowed_content_types"]),
        )
    raise JsonSchemaIngestionError(
        f"URL '{url}' is not allowlisted for JSON Schema ingestion of tool '{tool_id}'."
    )


def list_json_schema_sources_for_tool(tool_id: str) -> list[str]:
    normalized_tool_id = tool_id.strip().casefold()
    data = _json_schema_ingestion_sources()
    tool = data["tools"].get(normalized_tool_id)
    if tool is None:
        return []
    return [str(item["url"]) for item in tool["sources"]]


@cache
def _json_schema_ingestion_sources() -> dict[str, Any]:
    with _JSON_SCHEMA_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_json_schema_ingestion_sources(data)
    return data


def _validate_json_schema_ingestion_sources(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("JSON Schema ingestion source contract must have version=1")
    required = (
        "default_timeout_seconds",
        "default_max_bytes",
        "default_cache_ttl_seconds",
        "max_redirects",
        "max_fields_per_schema",
        "allowed_content_types",
        "tools",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"JSON Schema ingestion source contract missing {key}")
    for key in (
        "default_timeout_seconds",
        "default_max_bytes",
        "max_fields_per_schema",
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

    trust = _trust_sources()
    aggregator_hosts = schema_aggregator_hosts("json_schema")
    tools = data["tools"]
    if not isinstance(tools, dict) or not tools:
        raise ValueError("tools must be a non-empty mapping")
    for tool_id, tool in tools.items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("invalid tool id in JSON Schema ingestion sources")
        if tool_id not in trust["tools"]:
            raise ValueError(
                f"JSON Schema ingestion tool '{tool_id}' is not in tool_trust_sources contract"
            )
        official_hosts = set(trust["tools"][tool_id]["official_hosts"]) | set(
            aggregator_hosts
        )
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
                    f"tool '{tool_id}' source host {hostname!r} is not in official_hosts "
                    "or schema_aggregator_hosts"
                )
            EvidenceType(source["evidence_type"])


def _allowed_hosts_for_tool(tool_id: str) -> frozenset[str]:
    trust = _trust_sources()
    tool = trust["tools"].get(tool_id)
    if not tool:
        return frozenset()
    official = set(tool["official_hosts"])
    aggregators = set(schema_aggregator_hosts("json_schema"))
    return frozenset(official | aggregators)


# -------- Field iteration + claim generation --------


@dataclass(frozen=True)
class _SchemaField:
    name: str
    node: dict[str, Any]
    json_pointer: str  # without the leading '#'


def _iter_top_level_fields(
    document: dict[str, Any], max_fields: int
) -> list[_SchemaField]:
    properties = document.get("properties")
    if not isinstance(properties, dict):
        return []
    out: list[_SchemaField] = []
    for name in sorted(properties.keys()):
        node = properties[name]
        if not isinstance(name, str) or not isinstance(node, dict):
            continue
        out.append(
            _SchemaField(
                name=name,
                node=node,
                json_pointer=f"/properties/{_escape_json_pointer(name)}",
            )
        )
        if len(out) >= max_fields:
            return out
    return out


def _required_set(document: dict[str, Any]) -> set[str]:
    required = document.get("required")
    if not isinstance(required, list):
        return set()
    return {item for item in required if isinstance(item, str)}


def _escape_json_pointer(value: str) -> str:
    """RFC 6901 escaping: ~ -> ~0 and / -> ~1."""
    return value.replace("~", "~0").replace("/", "~1")


def _claims_from_field(
    *,
    spec: JsonSchemaFetchSpec,
    artifact: RawIngestionArtifact,
    fetch: JsonSchemaFetchResult,
    field: _SchemaField,
    is_required: bool,
    submitted_by: str,
    created_at: datetime,
) -> list[KnowledgeClaim]:
    prefix = spec.subject_prefix or spec.tool_id
    subject_base = f"{prefix}: {field.name}"[:_SUBJECT_MAX_CHARS]
    description = field.node.get("description") or field.node.get("title") or ""
    requiredness = "required" if is_required else "optional"
    type_hint = field.node.get("type") or "any"
    if isinstance(type_hint, list):
        type_hint = "/".join(str(t) for t in type_hint)
    statement = (
        f"{prefix} accepts a {requiredness} field `{field.name}` (type: {type_hint})."
    )
    if description:
        statement = f"{statement} {' '.join(str(description).split())}"
    statement = statement[:_STATEMENT_MAX_CHARS]

    claims: list[KnowledgeClaim] = [
        _build_claim(
            spec=spec,
            artifact=artifact,
            fetch=fetch,
            claim_type=ClaimType.CONFIG_FIELD_EXISTS,
            subject=subject_base,
            statement=statement,
            risk_level=RiskLevel.LOW,
            pointer=field.json_pointer,
            submitted_by=submitted_by,
            created_at=created_at,
        )
    ]

    if field.node.get("deprecated") is True:
        claims.append(
            _build_claim(
                spec=spec,
                artifact=artifact,
                fetch=fetch,
                claim_type=ClaimType.FEATURE_DEPRECATED,
                subject=f"{subject_base} (deprecated)"[:_SUBJECT_MAX_CHARS],
                statement=(
                    f"Config field `{field.name}` in {prefix} is marked deprecated."
                )[:_STATEMENT_MAX_CHARS],
                risk_level=RiskLevel.MEDIUM,
                pointer=f"{field.json_pointer}/deprecated",
                submitted_by=submitted_by,
                created_at=created_at,
            )
        )

    return claims


def _build_claim(
    *,
    spec: JsonSchemaFetchSpec,
    artifact: RawIngestionArtifact,
    fetch: JsonSchemaFetchResult,
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
    result: JsonSchemaFetchResult,
    captured_at: datetime,
) -> RawIngestionArtifact:
    artifact_id = generate_ingestion_artifact_id()
    source_uri = f"ayiru://ingestion/{run_id}/artifacts/{artifact_id}"
    return RawIngestionArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        artifact_type=IngestionArtifactType.JSON_SCHEMA_DOC,
        source_uri=source_uri,
        raw_content=result.raw_body,
        hash=f"sha256:{sha256(result.raw_body.encode('utf-8')).hexdigest()}",
        captured_at=captured_at,
    )


__all__ = [
    "HttpxJsonSchemaClient",
    "JsonSchemaFetchResult",
    "JsonSchemaFetchSpec",
    "JsonSchemaHttpClient",
    "JsonSchemaIngestionError",
    "JsonSchemaIngestionService",
    "json_schema_fetch_spec",
    "list_json_schema_sources_for_tool",
]
