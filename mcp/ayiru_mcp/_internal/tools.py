"""MCP tool registry for Ayiru.

Each `McpTool` carries the metadata MCP clients need to discover and
invoke it (`name`, `description`, `inputSchema`) plus a sync `handler`
that takes the call's arguments + a `ClaimStore` and returns a JSON-
serialisable dict.

Tool handlers ARE allowed to raise — the dispatcher in `server.py`
catches every exception and converts it to a structured error response
(MCP's `isError=True` content block) so a misbehaving tool never crashes
the JSON-RPC framing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.schemas.claim import ClaimCreate, KnowledgeClaim
from app.schemas.evidence import Evidence
from app.schemas.query import (
    AskRequest,
    ExplainRiskRequest,
    GetCapabilitiesRequest,
    GetConstraintsRequest,
    GetEffectsRequest,
    GetWorkflowPlanRequest,
    ResolveActionRequest,
    ResolveSubjectRequest,
    SafeWorkflowRequest,
    ValidateCommandRequest,
)
from app.services.claim_store import (
    ClaimStore,
    DuplicateClaimError,
    DuplicateEvidenceError,
    EvidencePolicyError,
    ToolNotAllowedError,
)
from app.services.evidence_trust import normalize_evidence_trust
from app.services.ids import generate_claim_id, generate_evidence_id
from app.services.orchestrator import CanonOrchestrator
from app.services.query_engine import QueryEngine


@dataclass(frozen=True)
class McpTool:
    """A single tool exposed over MCP.

    ``annotations`` carries the MCP 2025-06-18 ``ToolAnnotations`` object
    (https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
    The 2026-05-22 dogfood session caught a real bug: without annotations,
    Claude Desktop falls back to name-prefix heuristics (auto-trusts
    `get_*`/`search_*`/`explain_*`, gates everything else) and hides our
    `ask`, `validate_command`, `submit_claim` tools from the LLM. Setting
    ``readOnlyHint: true`` on the read tools tells the host explicitly
    that the tool is safe to expose, overriding the heuristic.

    Annotation fields (per spec):
      - ``title``: human-readable display name shown in the host's UI
      - ``readOnlyHint``: tool does not modify environment (default: false)
      - ``destructiveHint``: tool may have destructive side effects (default: true)
      - ``idempotentHint``: repeated calls with same args have same effect (default: false)
      - ``openWorldHint``: tool interacts with external systems (default: true)
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], ClaimStore], dict[str, Any]]
    annotations: dict[str, Any] | None = None
    # When False, the tool is registered (find_tool returns it, tools/call
    # routes to its handler) but does NOT appear in tools/list responses.
    # Used to hide writeable surfaces from the bundled MCP wheel whose
    # catalog lives in a read-only site-packages directory.
    advertised: bool = True


# -------- Tool handlers --------


def _handle_ask(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """`POST /query/ask` over MCP — the headline v0.2 retrieval surface."""
    request = AskRequest.model_validate(arguments)
    response = QueryEngine(store).ask(
        question=request.question,
        limit=request.limit,
        tool_id_hint=request.tool_id_hint,
    )
    return response.model_dump(mode="json")


def _handle_validate_command(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """`POST /query/validate-command` over MCP."""
    request = ValidateCommandRequest.model_validate(arguments)
    response = QueryEngine(store).validate_command(
        tool_id=request.tool_id, command=request.command
    )
    return response.model_dump(mode="json")


def _handle_get_tool_spec(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """`GET /query/tools/{tool_id}` over MCP."""
    tool_id = str(arguments.get("tool_id", "")).strip()
    if not tool_id:
        raise ValueError("tool_id is required")
    spec = QueryEngine(store).get_tool_spec(tool_id=tool_id)
    if spec is None:
        # Surface as a not-found result; the server wraps it as `isError`
        # so callers can distinguish "no canonical spec" from "server
        # crashed."
        raise LookupError(
            f"No canonical ToolSpec published for tool_id '{tool_id}'."
        )
    return spec.model_dump(mode="json")


def _handle_search_tools(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """`GET /query/search-tools` over MCP."""
    query = str(arguments.get("query", ""))
    limit = int(arguments.get("limit", 100))
    offset = int(arguments.get("offset", 0))
    response = QueryEngine(store).search_tools(
        query=query, limit=limit, offset=offset
    )
    return response.model_dump(mode="json")


def _handle_explain_risk(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """`POST /query/explain-risk` over MCP."""
    request = ExplainRiskRequest.model_validate(arguments)
    response = QueryEngine(store).explain_risk(
        tool_id=request.tool_id, action=request.action
    )
    return response.model_dump(mode="json")


def _handle_get_safe_workflow(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """`POST /query/safe-workflow` over MCP."""
    request = SafeWorkflowRequest.model_validate(arguments)
    response = QueryEngine(store).find_safe_workflows(
        goal=request.goal, environment=request.environment
    )
    return response.model_dump(mode="json")


def _handle_submit_claim(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """`POST /claims` over MCP — the only write tool.

    Mirrors the REST handler in `routes_claims.py`: server-assigns ids and
    `created_at`, normalises evidence trust, persists the claim, runs the
    orchestrator, and returns the resulting `KnowledgeClaim` with its
    verification status set."""
    # Validate top-level shape first so a totally-malformed payload (no
    # claim_type, no subject, etc.) returns the right "field X is required"
    # error message instead of being short-circuited by a downstream
    # check. The evidence-minimum check runs after this so a payload with
    # every field set but `evidence: []` gets a specific "needs at least
    # one piece of evidence" error.
    request = ClaimCreate.model_validate(arguments)
    if not request.evidence:
        # The Pydantic model allows empty `evidence` (only `max_length` is
        # declared). The MCP tool's inputSchema documents `minItems: 1`;
        # enforce it here so the surface contract matches the schema.
        raise ValueError(
            "submit_claim requires at least one piece of evidence."
        )
    now = datetime.now(timezone.utc)
    evidence_records: list[Evidence] = []
    for raw_evidence in request.evidence:
        normalized = normalize_evidence_trust(
            request.tool_id,
            Evidence(
                evidence_id=generate_evidence_id(),
                evidence_type=raw_evidence.evidence_type,
                source_uri=raw_evidence.source_uri,
                excerpt=raw_evidence.excerpt,
                hash=raw_evidence.hash,
                captured_at=raw_evidence.captured_at,
                trust_level=raw_evidence.trust_level,
            ),
        )
        evidence_records.append(normalized)
    claim = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=request.claim_type,
        subject=request.subject,
        statement=request.statement,
        tool_id=request.tool_id,
        submitted_by=request.submitted_by,
        evidence=evidence_records,
        risk_level=request.risk_level,
        created_at=now,
    )
    try:
        store.create(claim)
    except (
        DuplicateClaimError,
        DuplicateEvidenceError,
        EvidencePolicyError,
        ToolNotAllowedError,
    ) as exc:
        # Surface as an error result; orchestrator never ran.
        raise ValueError(str(exc)) from exc
    result = CanonOrchestrator(store).verify_claim(claim)
    store.save_verification_result(result)
    # Reload so we return the post-verification view of the claim.
    persisted = store.get(claim.claim_id)
    assert persisted is not None  # we just created it
    return persisted.model_dump(mode="json")


# -------- Structured-substrate handlers (v0.2 headline surface) --------
#
# The seven tools below mirror the structured query surfaces on
# `QueryEngine`. They return typed records — `CapabilityRecord`,
# `SubjectSummary`, etc. — not natural-language `statement` text. This is
# the "machine-readable external knowledge layer for AI agents" surface:
# agents call these BEFORE acting, not for prose answers.


def _handle_resolve_subject(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """Discovery: turn a fuzzy hint into a typed list of subjects."""
    request = ResolveSubjectRequest.model_validate(arguments)
    response = QueryEngine(store).resolve_subject(
        subject_hint=request.subject_hint,
        kind=request.kind,
        family_hint=request.family_hint,
        limit=request.limit,
    )
    return response.model_dump(mode="json")


def _handle_get_subject_spec(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """Once subject_id is known, return its full typed spec."""
    subject_id = str(arguments.get("subject_id", "")).strip()
    if not subject_id:
        raise ValueError("subject_id is required")
    spec = QueryEngine(store).get_subject_spec(subject_id=subject_id)
    if spec is None:
        raise LookupError(
            f"No subject spec found for subject_id '{subject_id}'."
        )
    return spec.model_dump(mode="json")


def _handle_get_capabilities(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """Typed capability records for a subject — invocations, configs, etc."""
    request = GetCapabilitiesRequest.model_validate(arguments)
    response = QueryEngine(store).get_capabilities(
        subject_id=request.subject_id,
        capability_types=list(request.capability_types) or None,
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
        verification_min=request.verification_min,
        limit=request.limit,
    )
    return response.model_dump(mode="json")


def _handle_get_constraints(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """Typed constraint records — auth scopes, env preconditions, etc."""
    request = GetConstraintsRequest.model_validate(arguments)
    response = QueryEngine(store).get_constraints(
        subject_id=request.subject_id,
        action_intent=request.action_intent,
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
    )
    return response.model_dump(mode="json")


def _handle_get_effects(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """Typed effect profile — destructive, mutates_remote_state, reversible."""
    request = GetEffectsRequest.model_validate(arguments)
    response = QueryEngine(store).get_effects(
        subject_id=request.subject_id,
        action_intent=request.action_intent,
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
    )
    return response.model_dump(mode="json")


def _handle_resolve_action(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """Ground an intended action: top capability + constraints + effects."""
    request = ResolveActionRequest.model_validate(arguments)
    response = QueryEngine(store).resolve_action(
        subject_id=request.subject_id,
        action_intent=request.action_intent,
        command=request.command,
        environment=request.environment,
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
        limit=request.limit,
    )
    return response.model_dump(mode="json")


def _handle_get_workflow_plan(
    arguments: dict[str, Any], store: ClaimStore
) -> dict[str, Any]:
    """Goal-matched workflow plans, safest-first."""
    request = GetWorkflowPlanRequest.model_validate(arguments)
    response = QueryEngine(store).get_workflow_plan(
        goal=request.goal,
        environment=request.environment,
        limit=request.limit,
    )
    return response.model_dump(mode="json")


# -------- Tool registry --------


_TOOL_REGISTRY: list[McpTool] = [
    # -------- Structured substrate (v0.2 headline) --------
    #
    # Ayiru's machine-readable surface. Agents call these BEFORE acting on
    # a developer tool. The order matters — `resolve_subject` is first so
    # an LLM doing discovery reaches for it before anything else (Stage 17
    # found tool order biases LLM tool choice).
    McpTool(
        name="resolve_subject",
        description=(
            "FIRST CALL before acting on any developer tool, API, SDK, or "
            "workflow. Resolves a fuzzy human-facing hint (e.g. 'open a "
            "pull request', 'gh pr create', 'github') to a typed list of "
            "subjects with `subject_id`s you can then feed to "
            "`get_capabilities`, `get_constraints`, `get_effects`, or "
            "`resolve_action`. Returns SubjectSummary records (typed: "
            "subject_id, subject_kind, family, capability_count, "
            "verification_level) — NO prose. If the LLM is uncertain "
            "which tool the user means, call this first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject_hint": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Fuzzy hint — a verb phrase, a tool name, a "
                        "subcommand, an API path, etc. Examples: 'gh pr "
                        "create', 'delete a docker volume', 'kubectl get "
                        "pods'."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": ["tool", "api", "sdk", "adk", "workflow", "subject"],
                    "description": (
                        "Optional kind filter when the hint is ambiguous "
                        "across kinds."
                    ),
                },
                "family_hint": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                    "description": (
                        "Optional family narrower (e.g. 'gh', 'docker'). "
                        "Useful when the subject_hint alone is too generic."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            "required": ["subject_hint"],
            "additionalProperties": False,
        },
        handler=_handle_resolve_subject,
        annotations={
            "title": "Resolve subject",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    McpTool(
        name="get_subject_spec",
        description=(
            "Return the full typed spec for a known `subject_id` "
            "(SubjectSpecResponse: name, family, interfaces, capabilities, "
            "workflows, verification_level, provenance_claim_ids). Use "
            "this after `resolve_subject` when you need the spec body, "
            "not just the summary. Returns null-equivalent error if the "
            "subject is unknown."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                    "description": (
                        "Canonical subject id from `resolve_subject` "
                        "(e.g. 'gh-pr-create')."
                    ),
                },
            },
            "required": ["subject_id"],
            "additionalProperties": False,
        },
        handler=_handle_get_subject_spec,
        annotations={
            "title": "Get subject spec",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    McpTool(
        name="get_capabilities",
        description=(
            "THE structured surface. Returns typed CapabilityRecord rows "
            "for a subject — `capability_type` ∈ {invocation, "
            "configuration, constraint, effect, environment, deprecation, "
            "workflow, metadata}, structured `detail` dicts, and "
            "verification metadata. The current bundled catalog is "
            "machine-readable only: rows are ingested from real `--help` "
            "output with typed argv / flag fields. "
            "`accepted_only_structured` remains for backward-compatibility "
            "with older mixed catalogs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                },
                "capability_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "existence", "invocation", "configuration",
                            "constraint", "effect", "environment",
                            "deprecation", "workflow", "metadata",
                        ],
                    },
                    "maxItems": 20,
                    "default": [],
                    "description": "Filter to these capability types only.",
                },
                "accepted_only": {"type": "boolean", "default": True},
                "accepted_only_structured": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true, returns only typed structured rows. "
                        "Useful when querying older mixed catalogs; the "
                        "current bundled catalog is already structured-only."
                    ),
                },
                "verification_min": {
                    "type": "string",
                    "enum": [
                        "L0_unverified", "L1_schema_valid",
                        "L2_source_verified", "L3_runtime_verified",
                    ],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "required": ["subject_id"],
            "additionalProperties": False,
        },
        handler=_handle_get_capabilities,
        annotations={
            "title": "Get capabilities",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    McpTool(
        name="get_constraints",
        description=(
            "Typed constraint records — what the agent must satisfy "
            "BEFORE running an action: auth scopes, environment "
            "preconditions, deprecation status. Call this before "
            "`resolve_action` if you want to surface gating requirements "
            "early."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                },
                "action_intent": {
                    "type": "string",
                    "maxLength": 512,
                    "description": (
                        "Optional intent narrower (e.g. 'create a PR "
                        "with reviewers')."
                    ),
                },
                "accepted_only": {"type": "boolean", "default": True},
                "accepted_only_structured": {
                    "type": "boolean", "default": False,
                },
            },
            "required": ["subject_id"],
            "additionalProperties": False,
        },
        handler=_handle_get_constraints,
        annotations={
            "title": "Get constraints",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    McpTool(
        name="get_effects",
        description=(
            "Typed effect profile — destructive, mutates_remote_state, "
            "reversible, may_cost_money, may_expose_secrets. Returns "
            "EffectProfileResponse with aggregate_risk_level and "
            "requires_confirmation. Call this before executing any "
            "action that could change remote state."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                },
                "action_intent": {"type": "string", "maxLength": 512},
                "accepted_only": {"type": "boolean", "default": True},
                "accepted_only_structured": {
                    "type": "boolean", "default": False,
                },
            },
            "required": ["subject_id"],
            "additionalProperties": False,
        },
        handler=_handle_get_effects,
        annotations={
            "title": "Get effects",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    McpTool(
        name="resolve_action",
        description=(
            "End-to-end action grounding. Given a subject_id + intent + "
            "optional literal command, returns the matched capability, "
            "supporting capabilities, constraints, effects, "
            "safe_to_auto_execute, requires_human_confirmation, and "
            "verdict reasons. The one-shot machine-readable answer for "
            "'what should I run, what does it need, what does it do'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                },
                "action_intent": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                },
                "command": {
                    "type": "string",
                    "maxLength": 512,
                    "description": (
                        "Optional literal command the agent is "
                        "considering running. When set, the response "
                        "resolves via command_match instead of "
                        "capability search."
                    ),
                },
                "environment": {"type": "string", "maxLength": 128},
                "accepted_only": {"type": "boolean", "default": True},
                "accepted_only_structured": {
                    "type": "boolean", "default": False,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1, "maximum": 20, "default": 5,
                },
            },
            "required": ["subject_id", "action_intent"],
            "additionalProperties": False,
        },
        handler=_handle_resolve_action,
        annotations={
            "title": "Resolve action",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    McpTool(
        name="get_workflow_plan",
        description=(
            "Goal-matched workflow plans, safest-first. Returns typed "
            "WorkflowPlanSummary records (workflow_id, subject_ids, "
            "step_count, aggregate_risk_level, verification_level, "
            "requires_confirmation). Use when the user states a "
            "multi-step goal ('deploy a Helm release', 'cut a GitHub "
            "release') rather than a single command."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "minLength": 1, "maxLength": 512,
                },
                "environment": {"type": "string", "maxLength": 128},
                "limit": {
                    "type": "integer",
                    "minimum": 1, "maximum": 100, "default": 20,
                },
            },
            "required": ["goal"],
            "additionalProperties": False,
        },
        handler=_handle_get_workflow_plan,
        annotations={
            "title": "Get workflow plan",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    # -------- Legacy prose surfaces (handlers preserved, hidden from tools/list) --------
    #
    # These six tools shipped in v0.1 and remain registered so any pinned
    # external config still routes correctly. They are NOT in `tools/list`
    # because the v0.2 thesis is structured-first: agents should reach for
    # the typed surfaces above, not prose responses. `find_tool()` still
    # resolves them, so `tools/call ask {...}` continues to work.
    McpTool(
        name="ask",
        # The description is what the LLM sees when picking which tool
        # to invoke. The 2026-05-22 dogfood session caught a real LLM
        # meta-policy: by default, the model only reaches for tools
        # when it's *uncertain*. Stable technical facts ("how do I
        # docker rm") get answered from training memory, not from a
        # tool — even when a verified-citation tool is available.
        # This description has to override that meta-policy explicitly:
        # the user trusts THIS graph more than the model's memory,
        # so the model should defer even when it's confident.
        #
        # KEEP IN SYNC with the LangChain adapter at
        # clients/python/ayiru_client/langchain.py::_DESCRIPTION.
        # Both surfaces hit the same LLM meta-policy; the wording is
        # load-bearing and changing it in one place without the other
        # will create a behavior drift between MCP and LangChain hosts.
        description=(
            "Use this before acting on a developer tool, API, SDK, workflow, "
            "or config surface. Ayiru is a machine-readable external "
            "knowledge layer for agents: it returns cited, verification-"
            "graded answers drawn from official docs, source code, and man "
            "pages. Prefer ACCEPTED answers first; if Ayiru returns "
            "`answer_status: informational`, the answer is cited but still "
            "review-pending and should be treated as informational rather "
            "than authoritative. Even when you already know the answer from "
            "training, prefer this tool because the user explicitly wants "
            "cited, auditable knowledge before action. Only fall back to "
            "your training data when the response includes "
            "`fallback_recommended: true`. When the question is about a "
            "specific tool, set `tool_id_hint` to that tool's family name "
            "(e.g. 'ffmpeg', 'docker', 'git') — Ayiru expands it across "
            "the tool's command, config, recipe, and error surfaces for a "
            "sharper match."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": (
                        "The natural-language question, e.g. "
                        "'how do I delete a docker container' or "
                        "'what does gh repo delete --yes do'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                    "description": "Max number of answers to return.",
                },
                "tool_id_hint": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                    "description": (
                        "Optional but recommended whenever the question "
                        "names a specific tool. Pass the tool's family "
                        "name (e.g. 'ffmpeg', 'docker', 'git', 'kubectl') "
                        "and Ayiru auto-expands it to that tool's "
                        "documentation surfaces (-cli commands, -config, "
                        "-recipes, -errors, and a tool-specific topic), so "
                        "you don't need to know the exact surface id. An "
                        "exact surface id (e.g. 'docker-cli') also works. "
                        "Setting this sharply narrows the search and "
                        "improves match quality."
                    ),
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        handler=_handle_ask,
        # MCP 2025-06-18 annotations — declare ask as pure-read so
        # Claude Desktop's tool-gating heuristic exposes it to the LLM
        # alongside the get_*/search_*/explain_* tools. Without this,
        # the 2026-05-22 dogfood showed Claude Desktop hiding `ask`.
        annotations={
            "title": "Look up a verified answer",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        advertised=False,
    ),
    McpTool(
        name="validate_command",
        description=(
            "Check whether a command is safe to run on a specific tool. "
            "Returns a structured safety verdict: {safe_to_auto_execute, "
            "risk_level, requires_human_confirmation, verification_level, "
            "confidence, reasons, evidence}. Default-deny on no match."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                    "description": "The tool's canonical id, e.g. 'git', 'github-cli'.",
                },
                "command": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": "The exact command string the agent is considering running.",
                },
            },
            "required": ["tool_id", "command"],
            "additionalProperties": False,
        },
        handler=_handle_validate_command,
        annotations={
            "title": "Check command safety",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        advertised=False,
    ),
    McpTool(
        name="get_tool_spec",
        description=(
            "Retrieve the canonical ToolSpec for a tool — its capabilities, "
            "commands, risk profile, and provenance. Returns the full spec "
            "object or an error if no spec has been published for the tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                },
            },
            "required": ["tool_id"],
            "additionalProperties": False,
        },
        handler=_handle_get_tool_spec,
        annotations={
            "title": "Get tool specification",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        advertised=False,
    ),
    McpTool(
        name="search_tools",
        description=(
            "Search across published ToolSpecs by tool_id, name, or "
            "capability substring. Returns a paginated list of matches "
            "with verification level and highest risk level. Use empty "
            "query to list all known tools."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 256},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
        handler=_handle_search_tools,
        annotations={
            "title": "Search tools",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        advertised=False,
    ),
    McpTool(
        name="explain_risk",
        description=(
            "Run the deterministic risk classifier against an action and "
            "return its risk level, six boolean risk dimensions "
            "(destructive_action, mutates_remote_state, reversible, "
            "requires_auth, may_cost_money, may_expose_secrets), reasons, "
            "and any citing claims."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                },
                "action": {"type": "string", "minLength": 1, "maxLength": 512},
            },
            "required": ["tool_id", "action"],
            "additionalProperties": False,
        },
        handler=_handle_explain_risk,
        annotations={
            "title": "Explain risk classification",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        advertised=False,
    ),
    McpTool(
        name="get_safe_workflow",
        description=(
            "Find published WorkflowSpecs matching a goal, sorted safest-"
            "first (lowest aggregate risk first). `environment` is accepted "
            "for future filtering but is currently echoed back, not "
            "filtered on."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "minLength": 1, "maxLength": 512},
                "environment": {"type": "string", "maxLength": 128},
            },
            "required": ["goal"],
            "additionalProperties": False,
        },
        handler=_handle_get_safe_workflow,
        annotations={
            "title": "Get safe workflow",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        advertised=False,
    ),
    McpTool(
        name="submit_claim",
        description=(
            "Submit a structured KnowledgeClaim with cited evidence. The "
            "orchestrator validates evidence, classifies risk, scores "
            "confidence, and emits a verification result. The claim's "
            "post-verification state (status, verification_level, "
            "confidence) is returned. Acceptance is NOT guaranteed — low-"
            "confidence or policy-violating claims surface as PENDING / "
            "REJECTED."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "claim_type": {"type": "string"},
                "subject": {"type": "string", "minLength": 1, "maxLength": 512},
                "statement": {"type": "string", "minLength": 1},
                "tool_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.-]{1,128}$",
                },
                "submitted_by": {"type": "string", "minLength": 1, "maxLength": 128},
                "risk_level": {"type": "string"},
                "evidence": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "evidence_type": {"type": "string"},
                            "source_uri": {"type": "string", "minLength": 1},
                            "excerpt": {"type": "string", "minLength": 1},
                            "hash": {"type": "string", "minLength": 1},
                            "captured_at": {"type": "string"},
                            "trust_level": {"type": "string"},
                        },
                        "required": [
                            "evidence_type",
                            "source_uri",
                            "excerpt",
                            "hash",
                            "captured_at",
                            "trust_level",
                        ],
                    },
                },
            },
            "required": [
                "claim_type",
                "subject",
                "statement",
                "tool_id",
                "submitted_by",
                "risk_level",
                "evidence",
            ],
            "additionalProperties": False,
        },
        handler=_handle_submit_claim,
        # submit_claim is the only write tool. We declare it accurately:
        # not read-only (it persists state), but not destructive either
        # (claims are append-only — they can't overwrite existing data).
        # Hosts should still require explicit user approval for writes,
        # which is fine — this is the one tool where gating is correct.
        annotations={
            "title": "Submit a knowledge claim",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        # Registered but hidden from tools/list. The bundled ayiru-mcp
        # catalog lives in a read-only site-packages directory, and the
        # "verified knowledge" pitch is read-only by design. Direct
        # invocation via tools/call (find_tool returns it) still works
        # for tests and for the FastAPI backend's writeable catalog mode.
        advertised=False,
    ),
]


def list_tools() -> list[McpTool]:
    """Tools surfaced to MCP clients via the `tools/list` request.

    Excludes tools marked `advertised=False`: the legacy prose surfaces
    (`ask`, `validate_command`, `get_tool_spec`, `search_tools`,
    `explain_risk`, `get_safe_workflow`) and `submit_claim`. Only the seven
    structured query tools are advertised. `find_tool` still resolves hidden
    tools, so `tools/call` against them works for pinned external callers,
    backend dev contexts, and tests.
    """
    return [tool for tool in _TOOL_REGISTRY if tool.advertised]


def find_tool(name: str) -> McpTool | None:
    for tool in _TOOL_REGISTRY:
        if tool.name == name:
            return tool
    return None


__all__ = [
    "McpTool",
    "find_tool",
    "list_tools",
]
