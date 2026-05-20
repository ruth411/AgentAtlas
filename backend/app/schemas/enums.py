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
    CONFIG_FIELD_EXISTS = "config_field_exists"


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


class IngestionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestionArtifactType(StrEnum):
    CLI_OUTPUT = "cli_output"
    DOCS_CONTENT = "docs_content"
    OPENAPI_SPEC = "openapi_spec"
    JSON_SCHEMA_DOC = "json_schema_doc"
    GRAPHQL_SDL = "graphql_sdl"
    MCP_TOOL_LIST = "mcp_tool_list"
    SANDBOX_EXECUTION_LOG = "sandbox_execution_log"


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


# -------- Stage 13: Human Review + Audit --------


class HumanReviewDecision(StrEnum):
    """The verdict a human reviewer renders on a claim.

    `APPROVED` promotes the claim to `L5_HUMAN_AUDITED` (if it has already
    cleared L3 — the orchestrator refuses to skip levels). `REJECTED`
    flips the claim's `verification_status` to `REJECTED` so the matcher
    excludes it. `NEEDS_CHANGES` records the reviewer's notes without
    changing level or status; the claim stays in the queue until the
    submitter addresses the notes and a follow-up review fires.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"


class AuditEventType(StrEnum):
    """Every state-changing operation in Ayiru emits one event.

    The audit log is append-only by contract: services may only INSERT
    rows, never UPDATE or DELETE. The event payload captures everything
    needed to replay decisions later (actor, entity, structured details).
    """

    CLAIM_SUBMITTED = "claim_submitted"
    VERIFICATION_RECORDED = "verification_recorded"
    HUMAN_REVIEW_RECORDED = "human_review_recorded"
    TOOL_SPEC_PUBLISHED = "tool_spec_published"
    WORKFLOW_SPEC_PUBLISHED = "workflow_spec_published"


class AuditEntityType(StrEnum):
    """Kind of object an audit event refers to.

    Indexed alongside `entity_id` so queries like "every event for claim
    X" do a single index lookup instead of a full scan.
    """

    CLAIM = "claim"
    TOOL_SPEC = "tool_spec"
    WORKFLOW_SPEC = "workflow_spec"
