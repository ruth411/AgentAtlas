from enum import StrEnum


class ClaimType(StrEnum):
    TOOL_EXISTS = "tool_exists"
    CLI_COMMAND_EXISTS = "cli_command_exists"
    CLI_FLAG_EXISTS = "cli_flag_exists"
    API_ENDPOINT_EXISTS = "api_endpoint_exists"
    MCP_TOOL_EXISTS = "mcp_tool_exists"
    AUTH_REQUIREMENT = "auth_requirement"
    SIDE_EFFECT = "side_effect"
    DESTRUCTIVE_ACTION = "destructive_action"
    ENVIRONMENT_REQUIREMENT = "environment_requirement"
    FEATURE_DEPRECATED = "feature_deprecated"
    WORKFLOW_STEP = "workflow_step"


class EvidenceType(StrEnum):
    OFFICIAL_DOCS = "official_docs"
    CLI_HELP_OUTPUT = "cli_help_output"
    MAN_PAGE = "man_page"
    OPENAPI_SCHEMA = "openapi_schema"
    JSON_SCHEMA = "json_schema"
    GRAPHQL_SCHEMA = "graphql_schema"
    MCP_TOOL_SCHEMA = "mcp_tool_schema"
    SOURCE_CODE = "source_code"
    PACKAGE_METADATA = "package_metadata"
    SANDBOX_EXECUTION = "sandbox_execution"
    RELEASE_NOTES = "release_notes"
    MAINTAINER_REVIEW = "maintainer_review"


class RiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class VerificationLevel(StrEnum):
    L0_UNVERIFIED = "L0_unverified"
    L1_SCHEMA_VALID = "L1_schema_valid"
    L2_SOURCE_VERIFIED = "L2_source_verified"
    L3_RUNTIME_VERIFIED = "L3_runtime_verified"
    L4_CROSS_AGENT_VERIFIED = "L4_cross_agent_verified"
    L5_HUMAN_AUDITED = "L5_human_audited"


VERIFICATION_LEVEL_ORDER: dict[VerificationLevel, int] = {
    item: index for index, item in enumerate(VerificationLevel)
}


class VerificationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CONFLICT_DETECTED = "conflict_detected"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class TrustLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OrchestratorDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING_MORE_EVIDENCE = "pending_more_evidence"
    DUPLICATE = "duplicate"
    CONFLICT_DETECTED = "conflict_detected"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class ConfidenceBand(StrEnum):
    """Actionability bands derived from a numeric confidence score.

    Bands map score ranges to consumer behavior. They are inclusive of
    the lower bound and exclusive of the upper bound, except `STRONG`
    which is inclusive on both ends.
    """

    NONE = "none"          # score < 0.30
    LOW = "low"            # 0.30 <= score < 0.55
    MODERATE = "moderate"  # 0.55 <= score < 0.75
    HIGH = "high"          # 0.75 <= score < 0.90
    STRONG = "strong"      # 0.90 <= score <= 1.00
