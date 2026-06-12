from urllib.parse import urlparse

from app.schemas.enums import EvidenceType, TrustLevel
from app.schemas.evidence import Evidence

TRUSTED_SOURCE_EVIDENCE_TYPES = {
    EvidenceType.OFFICIAL_DOCS,
    EvidenceType.CLI_HELP_OUTPUT,
    EvidenceType.MAN_PAGE,
    EvidenceType.OPENAPI_SCHEMA,
    EvidenceType.JSON_SCHEMA,
    EvidenceType.GRAPHQL_SCHEMA,
    EvidenceType.MCP_TOOL_SCHEMA,
    EvidenceType.SOURCE_CODE,
    EvidenceType.PACKAGE_METADATA,
    EvidenceType.SANDBOX_EXECUTION,
    EvidenceType.RELEASE_NOTES,
    EvidenceType.MAINTAINER_REVIEW,
}

# Positive allowlist of URL schemes. Anything else is rejected as evidence —
# a blacklist will always miss the next dangerous scheme (javascript:, data:,
# vbscript:, chrome-extension:, etc.). Add a scheme here only after auditing
# that downstream consumers (Stage 9 query surface, Stage 12 dashboard) handle
# it correctly without auto-fetching or rendering as a link.
#
# Notably excluded:
# - `llm`, `memory` — LLM reasoning / agent memory are rejected primary evidence per Stage 0.
# - `maintainer-review`, `sandbox` — these schemes are reserved for Stage 8 (real sandbox
#   issuer) and Stage 13 (signed maintainer review). Until those layers exist, accepting
#   the bare scheme would let any submitter self-assert maintainer or runtime trust.
# - `javascript`, `data`, `vbscript`, `ftp`, `chrome-extension`, etc. — never appropriate
#   for evidence and dangerous if any consumer renders the URI as a link.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset(
    {
        "http",
        "https",
        "ayiru",
        "local",
        "file",
        "docs",
    }
)

# Hosts excluded even within allowed schemes, because they encourage
# low-quality or maintainer-unverifiable claims.
REJECTED_HOST_SUFFIXES = (
    "stackoverflow.com",
    "gist.github.com",
)
REJECTED_HOST_LABELS = (
    "blog",
    "weblog",
    "medium",
)


def _hostname_labels(hostname: str) -> set[str]:
    return {label for label in hostname.split(".") if label}


def _is_rejected_source(source_uri: str) -> bool:
    parsed = urlparse(source_uri)
    scheme = (parsed.scheme or "").lower()
    if not scheme or scheme not in ALLOWED_URL_SCHEMES:
        return True

    # http/https URLs additionally undergo host-level checks; non-web allowed
    # schemes (`ayiru`, `local`, `file`, `docs`) carry no hostname and
    # are accepted once the scheme passes.
    if scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        # http/https URLs without a host are malformed — reject.
        return True

    if any(hostname == suffix or hostname.endswith("." + suffix) for suffix in REJECTED_HOST_SUFFIXES):
        return True

    if REJECTED_HOST_LABELS and _hostname_labels(hostname) & set(REJECTED_HOST_LABELS):
        return True

    return False


def evidence_policy_violations(evidence: list[Evidence]) -> list[str]:
    violations: list[str] = []

    for item in evidence:
        if _is_rejected_source(item.source_uri):
            violations.append(
                f"Evidence '{item.evidence_id}' relies on a rejected primary source."
            )
        if item.evidence_type not in TRUSTED_SOURCE_EVIDENCE_TYPES:
            violations.append(f"Evidence '{item.evidence_id}' has unsupported evidence type.")

    return violations


def has_trusted_source_evidence(evidence: list[Evidence]) -> bool:
    return any(
        item.evidence_type in TRUSTED_SOURCE_EVIDENCE_TYPES
        and item.trust_level in {TrustLevel.MEDIUM, TrustLevel.HIGH}
        for item in evidence
    )


def has_runtime_evidence(evidence: list[Evidence]) -> bool:
    return any(item.evidence_type == EvidenceType.SANDBOX_EXECUTION for item in evidence)


def independent_evidence_stream_count(evidence: list[Evidence]) -> int:
    return len({(item.evidence_type.value, item.source_uri) for item in evidence})
