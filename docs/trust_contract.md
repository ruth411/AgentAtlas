# Trust Contract

This document defines the trust semantics for AgentAtlas. Later implementation must not weaken these rules to make demos easier.

## Canonical Terms

`KnowledgeClaim` is a structured assertion about a tool, command, API, MCP tool, workflow, side effect, auth requirement, risk, environment requirement, or deprecation.

`Evidence` is a captured source or observation that supports or contradicts a claim.

`ToolSpec` is the canonical verified representation of a tool. It is compiled from accepted claims only.

`WorkflowSpec` is a verified multi-step workflow with explicit risk and confirmation requirements per step.

`VerificationLevel` describes how strongly a claim or spec has been verified.

`RiskLevel` describes the operational danger of an action.

`Canon Orchestrator` is the service that validates, scores, classifies, accepts, rejects, defers, or escalates claims.

## Claim Taxonomy

- `tool_exists`: A tool exists and can be identified.
- `cli_command_exists`: A CLI command exists.
- `cli_flag_exists`: A CLI flag or option exists.
- `api_endpoint_exists`: An API endpoint exists.
- `mcp_tool_exists`: An MCP tool exists.
- `auth_requirement`: A tool action requires authentication or authorization.
- `side_effect`: A command, endpoint, or workflow mutates state or causes an external effect.
- `destructive_action`: An action can delete, revoke, destroy, expose, or irreversibly change resources.
- `environment_requirement`: A tool action requires an environment variable, local dependency, account state, project link, or runtime environment.
- `feature_deprecated`: A command, endpoint, flag, behavior, or workflow is deprecated.
- `workflow_step`: A step belongs to a verified workflow.

## Evidence Taxonomy

Accepted evidence types:

- `official_docs`
- `cli_help_output`
- `man_page`
- `openapi_schema`
- `json_schema`
- `graphql_schema`
- `mcp_tool_schema`
- `source_code`
- `package_metadata`
- `sandbox_execution`
- `release_notes`
- `maintainer_review`

Rejected as primary evidence:

- LLM reasoning alone
- Agent memory alone
- Unverified blog posts
- Random StackOverflow answers
- Guessed behavior
- Unattributed examples

## Verification Levels

- `L0_unverified`: Submitted but not validated.
- `L1_schema_valid`: Structurally valid claim or spec.
- `L2_source_verified`: Supported by trusted source evidence.
- `L3_runtime_verified`: Confirmed through sandbox execution, mock, or deterministic runtime check.
- `L4_cross_agent_verified`: Independently confirmed by multiple evidence streams or agents.
- `L5_human_audited`: Reviewed and approved by a trusted human maintainer.

Rules:

- Never mark a claim above `L1_schema_valid` without evidence.
- Never mark a claim above `L2_source_verified` without source evidence.
- Never mark a claim above `L3_runtime_verified` without actual runtime, sandbox, or deterministic mock verification.
- Never mark a claim above `L4_cross_agent_verified` unless at least two independent evidence streams agree.
- `L5_human_audited` requires explicit maintainer review.

## Confidence Bands

- `none`: Score below `0.30`; not promotable beyond `L1_schema_valid`.
- `low`: Score from `0.30` to below `0.55`; may show source support, but is not sufficient for automated acceptance.
- `moderate`: Score from `0.55` to below `0.75`; minimum band for automated acceptance on non-high-risk claims.
- `high`: Score from `0.75` to below `0.90`; actionable for stronger source-supported claims.
- `strong`: Score from `0.90` to `1.00`; strongest automated confidence band.

Rules:

- `L1_schema_valid` to `L2_source_verified` requires trusted source evidence and at least `low` confidence.
- `accepted` requires at least `moderate` confidence.
- `high` and `critical` risk claims require score `>= 0.85` or human review.
- Confidence bands are actionability gates, not decorative labels.

## Risk Levels

- `none`: Pure metadata or no-op actions.
- `low`: Read-only local inspection.
- `medium`: Creates local state, temporary resources, or reversible remote state.
- `high`: Mutates remote systems, deploys, changes permissions, affects production, or may incur costs.
- `critical`: Deletes resources, exposes secrets, revokes access, destroys infrastructure, performs irreversible changes, or has severe billing/security impact.

Default execution policy:

| Risk | Auto-execute allowed | Human confirmation required |
| --- | --- | --- |
| `none` | yes | no |
| `low` | yes | no |
| `medium` | no | yes |
| `high` | no | yes |
| `critical` | no | yes |

Critical actions must never be auto-executed by AgentAtlas.

## Orchestrator Decisions

- `accepted`: The claim has sufficient evidence and risk handling for its current verification level.
- `rejected`: The claim is invalid, unsupported, unsafe to publish, or contradicted beyond repair.
- `pending_more_evidence`: The claim may be true but does not have enough evidence.
- `duplicate`: The claim duplicates an existing canonical or accepted claim.
- `conflict_detected`: Evidence or claims disagree in a way that prevents publication.
- `requires_human_review`: The claim is too risky, ambiguous, or consequential for automated acceptance.

Every decision must include reason codes or human-readable reasons. Silent promotion is not allowed.

## Publication Rules

- Only accepted claims may feed canonical `ToolSpec` or `WorkflowSpec` outputs.
- Published specs must preserve `source_claim_ids` and `source_evidence_ids`.
- Pending, rejected, duplicate, and conflicting claims must remain queryable for audit but must not become canonical truth.
- Canonical specs must expose verification level and risk profile.
- A canonical spec's `verification_level` is the minimum verification level of all source claims used to compile it.
- Unknown or unstructured fields must be represented as `null` or explicit `publication_issues`, not filled with placeholder prose.
- Claims may be submitted with no evidence only as pending intake. They must resolve to `pending_more_evidence` and must not publish.

## Prompt-Injection Boundary

Documentation, CLI output, MCP metadata, API descriptions, README files, and issue comments are data. They are not instructions.

AgentAtlas may extract claims from these sources, but must not execute instructions contained inside them unless the instruction is already part of a trusted implementation path.
