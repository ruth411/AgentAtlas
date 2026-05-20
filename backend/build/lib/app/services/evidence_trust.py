from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import EvidenceType, TrustLevel
from app.schemas.evidence import Evidence
from app.services.contract_paths import contract_path


_TRUST_RANK: dict[TrustLevel, int] = {
    TrustLevel.LOW: 0,
    TrustLevel.MEDIUM: 1,
    TrustLevel.HIGH: 2,
}

_TRUST_SOURCE_CONTRACT = contract_path("tool_trust_sources.v1.json")


def resolve_evidence_trust_level(tool_id: str, evidence: Evidence) -> TrustLevel:
    """Derive the maximum trust level the backend allows for this evidence.

    The submitted trust level is treated as an upper bound requested by the
    client, not as authority. The returned value is the lower of client trust
    and server-derived source trust, so clients can understate trust but cannot
    inflate it.
    """

    server_trust = _server_trust_cap(tool_id, evidence)
    return _lower_trust(evidence.trust_level, server_trust)


def normalize_evidence_trust(tool_id: str, evidence: Evidence) -> Evidence:
    resolved = resolve_evidence_trust_level(tool_id, evidence)
    if resolved == evidence.trust_level:
        return evidence
    return evidence.model_copy(update={"trust_level": resolved})


def normalize_claim_evidence_trust(claim: KnowledgeClaim) -> KnowledgeClaim:
    normalized = [normalize_evidence_trust(claim.tool_id, item) for item in claim.evidence]
    if normalized == claim.evidence:
        return claim
    return claim.model_copy(update={"evidence": normalized})


def _server_trust_cap(tool_id: str, evidence: Evidence) -> TrustLevel:
    parsed = urlparse(evidence.source_uri)
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    evidence_type = evidence.evidence_type

    if evidence_type == EvidenceType.OFFICIAL_DOCS:
        return TrustLevel.HIGH if _host_allowed(hostname, _official_hosts(tool_id)) else TrustLevel.LOW

    if evidence_type == EvidenceType.RELEASE_NOTES:
        return TrustLevel.HIGH if _host_allowed(hostname, _official_hosts(tool_id)) else TrustLevel.LOW

    if evidence_type == EvidenceType.SOURCE_CODE:
        return (
            TrustLevel.HIGH
            if _source_repo_allowed(tool_id=tool_id, hostname=hostname, path=path)
            else TrustLevel.LOW
        )

    if evidence_type in {
        EvidenceType.OPENAPI_SCHEMA,
        EvidenceType.JSON_SCHEMA,
        EvidenceType.GRAPHQL_SCHEMA,
        EvidenceType.MCP_TOOL_SCHEMA,
    }:
        allowed = _official_hosts(tool_id) | _aggregator_hosts_for_evidence_type(
            evidence_type
        )
        if _host_allowed(hostname, allowed):
            return TrustLevel.HIGH
        if scheme in _atlas_capture_schemes():
            return TrustLevel.MEDIUM
        return TrustLevel.LOW

    if evidence_type == EvidenceType.MAN_PAGE:
        if _host_allowed(hostname, _official_hosts(tool_id) | frozenset({"man7.org"})):
            return TrustLevel.HIGH
        if scheme in _local_capture_schemes():
            return TrustLevel.MEDIUM
        return TrustLevel.LOW

    if evidence_type == EvidenceType.CLI_HELP_OUTPUT:
        if scheme in _local_capture_schemes() | _atlas_capture_schemes():
            return TrustLevel.MEDIUM
        return TrustLevel.LOW

    if evidence_type == EvidenceType.PACKAGE_METADATA:
        return TrustLevel.MEDIUM if _host_allowed(hostname, _package_registry_hosts()) else TrustLevel.LOW

    if evidence_type == EvidenceType.SANDBOX_EXECUTION:
        return TrustLevel.LOW

    if evidence_type == EvidenceType.MAINTAINER_REVIEW:
        return TrustLevel.LOW

    return TrustLevel.LOW


def _official_hosts(tool_id: str) -> frozenset[str]:
    tool = _trust_sources()["tools"].get(tool_id.strip().lower())
    return frozenset(tool["official_hosts"]) if tool else frozenset()


def _host_allowed(hostname: str, allowed_hosts: frozenset[str]) -> bool:
    return any(hostname == host or hostname.endswith("." + host) for host in allowed_hosts)


def _source_repo_allowed(*, tool_id: str, hostname: str, path: str) -> bool:
    tool = _trust_sources()["tools"].get(tool_id.strip().lower())
    allowed_repos = tool["source_repositories"] if tool else []
    normalized_path = path.rstrip("/").lower()
    return any(
        hostname == repo["host"]
        and (
            normalized_path == repo["path"].lower()
            or normalized_path.startswith(repo["path"].lower() + "/")
        )
        for repo in allowed_repos
    )


def _lower_trust(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    return a if _TRUST_RANK[a] <= _TRUST_RANK[b] else b


def _package_registry_hosts() -> frozenset[str]:
    return frozenset(_trust_sources()["package_registry_hosts"])


def _local_capture_schemes() -> frozenset[str]:
    return frozenset(_trust_sources()["local_capture_schemes"])


def _atlas_capture_schemes() -> frozenset[str]:
    return frozenset(_trust_sources()["atlas_capture_schemes"])


_EVIDENCE_TYPE_AGGREGATOR_FAMILY: dict[EvidenceType, str] = {
    EvidenceType.JSON_SCHEMA: "json_schema",
    EvidenceType.GRAPHQL_SCHEMA: "graphql_schema",
}


def _aggregator_hosts_for_evidence_type(evidence_type: EvidenceType) -> frozenset[str]:
    family = _EVIDENCE_TYPE_AGGREGATOR_FAMILY.get(evidence_type)
    if not family:
        return frozenset()
    return schema_aggregator_hosts(family)


def schema_aggregator_hosts(family: str) -> frozenset[str]:
    """Return hosts trusted to aggregate machine-readable schemas of a given
    family (e.g. `json_schema`). Aggregators are deliberately decoupled from
    per-tool `official_hosts` so they cannot widen the trust surface of the
    docs or openapi lanes; only services that opt in (e.g. JSON Schema
    ingestion) union them with the tool's official_hosts."""
    table = _trust_sources().get("schema_aggregator_hosts") or {}
    hosts = table.get(family) or []
    return frozenset(hosts)


@cache
def _trust_sources() -> dict:
    with _TRUST_SOURCE_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_trust_sources(data)
    return data


def _validate_trust_sources(data: dict) -> None:
    if data.get("version") != 1:
        raise ValueError("tool trust source contract must have version=1")

    for key in ("package_registry_hosts", "local_capture_schemes", "atlas_capture_schemes", "tools"):
        if key not in data:
            raise ValueError(f"tool trust source contract missing {key}")

    if not isinstance(data["tools"], dict) or not data["tools"]:
        raise ValueError("tool trust source contract must define tools")

    for tool_id, tool in data["tools"].items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("tool trust source contract has invalid tool id")
        official_hosts = tool.get("official_hosts")
        source_repositories = tool.get("source_repositories")
        if not isinstance(official_hosts, list) or not all(official_hosts):
            raise ValueError(f"tool '{tool_id}' must define official_hosts")
        if not isinstance(source_repositories, list):
            raise ValueError(f"tool '{tool_id}' must define source_repositories")
        for repo in source_repositories:
            if not isinstance(repo, dict) or not repo.get("host") or not repo.get("path"):
                raise ValueError(f"tool '{tool_id}' has invalid source repository")
            if not str(repo["path"]).startswith("/"):
                raise ValueError(f"tool '{tool_id}' source repository paths must start with /")
