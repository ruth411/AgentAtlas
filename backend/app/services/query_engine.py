"""Stage 9: Agent Query Surface — engine layer.

Pure read API on top of the existing claim store, risk classifier, and
canonical specs. Day 2 ships `validate_command` and `explain_risk`; Day 3
will add `search_tools`, `get_tool_spec`, and `find_safe_workflows`.

Design rules (all enforced by tests):

- Default-deny. No-match always returns `safe_to_auto_execute=False`.
- Best-verified-claim wins. The matcher's tie-breakers (level, confidence,
  recency) are authoritative; the engine does not re-pick.
- Confidence AND verification level both gate auto-execution. Even a low-
  risk match below the contract thresholds returns `safe=False` with the
  gate spelled out in `reasons`.
- Risk classification is run redundantly against the matched subject. If
  the classifier's output is HIGHER than the claim's stored risk, the
  classifier wins ("understated risk" defence; mirrors the orchestrator's
  acceptance pipeline).
- No writes. Every method on `QueryEngine` is read-only.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import cache
import json
from typing import Any

from app.schemas.enums import (
    RISK_ORDER,
    VERIFICATION_LEVEL_ORDER,
    ConfidenceBand,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.schemas.query import (
    EvidenceCitation,
    ExplainRiskResponse,
    RiskDimensions,
    SafeWorkflowResponse,
    SearchToolsResponse,
    ToolMatchSummary,
    ValidateCommandResponse,
    WorkflowSummary,
)
from app.schemas.risk import RiskAssessment, RiskDimension
from app.schemas.tool_spec import ToolSpec
from app.schemas.workflow_spec import WorkflowSpec
from app.services.claim_store import ClaimStore
from app.services.command_matcher import CommandMatch, match_command
from app.services.confidence_scorer import band_for_score
from app.services.contract_paths import contract_path
from app.services.risk_classifier import classify_action


_QUERY_POLICY_CONTRACT = contract_path("query_policy.v1.json")
_STAGE_0_CONTRACT = contract_path("ayiru_stage_0.v1.json")


class QueryEngine:
    def __init__(self, store: ClaimStore, *, now: datetime | None = None) -> None:
        self._store = store
        self._now = now

    # -------- validate_command --------

    def validate_command(
        self, *, tool_id: str, command: str
    ) -> ValidateCommandResponse:
        normalized_command = command.strip()
        match = match_command(
            tool_id=tool_id, command=normalized_command, store=self._store
        )
        if match is None:
            return _default_deny(
                tool_id=tool_id,
                command=normalized_command,
                generated_at=self._timestamp(),
            )
        return _verdict_from_match(
            tool_id=tool_id,
            command=normalized_command,
            match=match,
            generated_at=self._timestamp(),
        )

    # -------- explain_risk --------

    def explain_risk(
        self, *, tool_id: str, action: str
    ) -> ExplainRiskResponse:
        """Return the deterministic risk classification for an action plus
        any claims that match it as supporting citations.

        Always runs the risk classifier — even for unknown tools — because
        the classifier is content-driven (it matches against the action's
        text, not the tool's identity). Returns `risk_level=None` only if
        the classifier itself can't pick a label, which it won't (the engine
        always emits at least `RiskLevel.NONE`).
        """
        normalized = action.strip()
        assessment = classify_action(normalized, normalized, tool_id)
        citing_claim_ids: list[str] = []

        # Try to find a claim whose subject prefixes the action. This gives
        # us evidence-backed citations to attach to the explanation.
        match = match_command(
            tool_id=tool_id, command=normalized, store=self._store
        )
        if match is not None:
            citing_claim_ids.append(match.claim.claim_id)

        return ExplainRiskResponse(
            tool_id=tool_id,
            action=normalized,
            risk_level=assessment.risk_level,
            risk_dimensions=_dimensions_from_assessment(assessment),
            reasons=list(assessment.reasons),
            citing_claim_ids=citing_claim_ids,
            confidence=match.confidence if match is not None else 0.0,
            verdict_generated_at=self._timestamp(),
        )

    # -------- get_tool_spec --------

    def get_tool_spec(self, *, tool_id: str) -> ToolSpec | None:
        """Return the published canonical `ToolSpec` for a tool, or None.

        Thin pass-through to the store. Mounted under `/query/tools/{id}` so
        agents have one query surface instead of having to remember the
        `/canonical/` prefix. The route layer converts `None` into 404."""
        return self._store.get_canonical_tool_spec(tool_id.strip())

    # -------- search_tools --------

    def search_tools(
        self, *, query: str, limit: int = 100, offset: int = 0
    ) -> SearchToolsResponse:
        """Tiered substring search across published `ToolSpec` documents.

        Match priority (highest score wins; ties broken by `tool_id` ASC for
        deterministic ordering):

        - tool_id exact (case-insensitive) → score 100
        - tool_id substring                → score 80
        - name substring                   → score 60
        - capability substring             → score 40

        An empty `query` returns specs ordered by `tool_id` ASC — useful
        for "list every tool we cover" calls.
        """
        policy = _query_policy()
        max_limit = int(policy["max_search_limit"])
        if limit < 1:
            limit = 1
        if limit > max_limit:
            limit = max_limit
        if offset < 0:
            offset = 0

        normalized = query.strip().lower()

        # Pull every published spec. Tool count is small (<200 even at full
        # breadth), so loading + ranking in memory is fine. If this ever
        # blows up we'd add a SQL-side filter.
        all_specs = _paginate_all(
            lambda lim, off: self._store.list_canonical_tool_specs(
                limit=lim, offset=off
            ),
            page_size=int(policy["default_search_limit"]),
            hard_cap=10_000,
        )

        scored: list[tuple[int, str, ToolMatchSummary]] = []
        for spec in all_specs:
            score, reason = _score_tool_spec(spec, normalized)
            if score == 0 and normalized:
                continue
            summary = _summarise_tool_spec(spec, match_reason=reason)
            scored.append((score, spec.tool_id, summary))

        # Sort: score DESC, tool_id ASC. Pythonic via negative score + str.
        scored.sort(key=lambda triple: (-triple[0], triple[1]))
        total = len(scored)
        page = [summary for _, _, summary in scored[offset : offset + limit]]

        return SearchToolsResponse(
            query=query,
            matches=page,
            total=total,
            limit=limit,
            offset=offset,
        )

    # -------- find_safe_workflows --------

    def find_safe_workflows(
        self,
        *,
        goal: str,
        environment: str | None = None,
        limit: int = 20,
    ) -> SafeWorkflowResponse:
        """Substring match against published `WorkflowSpec.goal` text,
        sorted by aggregate risk ascending (safest first), then by score.

        `environment` is accepted on the request for future filtering — at
        v1, workflow specs don't yet carry environment metadata, so the
        engine echoes the field back in the response but does not filter
        on it. Stage 11 (seed dataset + dashboard) can add the filter once
        seeded workflows actually declare an environment.
        """
        policy = _query_policy()
        max_limit = int(policy["max_search_limit"])
        if limit < 1:
            limit = 1
        if limit > max_limit:
            limit = max_limit

        normalized = goal.strip().lower()

        all_workflows = _paginate_all(
            lambda lim, off: self._store.list_canonical_workflow_specs(
                limit=lim, offset=off
            ),
            page_size=int(policy["default_search_limit"]),
            hard_cap=10_000,
        )

        scored: list[tuple[int, RiskLevel, WorkflowSummary]] = []
        for spec in all_workflows:
            score = _score_workflow_spec(spec, normalized)
            if score == 0 and normalized:
                continue
            summary = _summarise_workflow_spec(spec)
            scored.append((score, summary.aggregate_risk_level, summary))

        # Safest first: aggregate_risk ASC, then score DESC, then workflow_id ASC.
        scored.sort(
            key=lambda triple: (
                RISK_ORDER[triple[1]],
                -triple[0],
                triple[2].workflow_id,
            )
        )
        total = len(scored)
        page = [summary for _, _, summary in scored[:limit]]

        return SafeWorkflowResponse(
            goal=goal,
            environment=environment,
            matches=page,
            total=total,
        )

    # -------- helpers --------

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)


# -------- Contract loaders --------


@cache
def _query_policy() -> dict[str, Any]:
    with _QUERY_POLICY_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_query_policy(data)
    return data


def _validate_query_policy(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("query_policy contract must have version=1")
    # `required` lists EVERY key the engine actually reads. If a key here is
    # missing, the engine raises KeyError at request time and the caller
    # gets a 500 — so the validator must be at least as strict as the engine.
    required = (
        "min_confidence_for_auto_execute",
        "min_verification_level_for_auto_execute",
        "command_max_length",
        "query_string_max_length",
        "default_search_limit",
        "max_search_limit",
        "match_method_priority",
        "default_deny_reason",
        "unknown_tool_reason",
        "low_verification_reason",
        "low_confidence_reason",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"query_policy contract missing {key}")
    threshold = data["min_confidence_for_auto_execute"]
    # bool is a subclass of int — reject it explicitly so `true` in JSON
    # can't sneak in as a threshold value.
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError(
            "min_confidence_for_auto_execute must be a number in [0, 1]"
        )
    if not (0.0 <= threshold <= 1.0):
        raise ValueError(
            "min_confidence_for_auto_execute must be in [0, 1]"
        )
    # Validate the verification level value is a real enum member.
    VerificationLevel(data["min_verification_level_for_auto_execute"])
    # Reason-text fields must be non-empty strings (an empty reason is
    # worse than a missing one — the response would carry "" instead of
    # surfacing a real explanation).
    for key in (
        "default_deny_reason",
        "unknown_tool_reason",
        "low_verification_reason",
        "low_confidence_reason",
    ):
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"query_policy contract {key} must be a non-empty string")
    # Limit fields must be positive ints; default must not exceed max.
    # Without this guard, a contract editor can set max_search_limit=0,
    # the engine clamps every request to limit=0, and the response then
    # fails Pydantic's `Field(ge=1)` and surfaces as a 500 to the caller.
    for key in ("default_search_limit", "max_search_limit"):
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"query_policy contract {key} must be a positive integer"
            )
    if data["default_search_limit"] > data["max_search_limit"]:
        raise ValueError(
            "query_policy contract default_search_limit must be <= max_search_limit"
        )
    # command_max_length is consumed by Pydantic at the API boundary; still
    # sanity-check it so a misconfigured value doesn't ship silently.
    for key in ("command_max_length", "query_string_max_length"):
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"query_policy contract {key} must be a positive integer"
            )


@cache
def _safety_policy_by_risk() -> dict[str, dict[str, Any]]:
    """Indexes Stage 0's safety_policy by risk level for O(1) lookup."""
    with _STAGE_0_CONTRACT.open() as handle:
        data = json.load(handle)
    out: dict[str, dict[str, Any]] = {}
    for rule in data["safety_policy"]:
        out[str(rule["risk_level"])] = rule
    return out


# -------- Verdict construction --------


def _default_deny(
    *,
    tool_id: str,
    command: str,
    generated_at: datetime,
) -> ValidateCommandResponse:
    policy = _query_policy()
    return ValidateCommandResponse(
        tool_id=tool_id,
        command=command,
        matched_claim_id=None,
        match_method="none",
        safe_to_auto_execute=False,
        requires_human_confirmation=True,
        risk_level=None,
        verification_level=VerificationLevel.L0_UNVERIFIED,
        confidence=0.0,
        confidence_band=ConfidenceBand.NONE,
        reasons=[
            policy["default_deny_reason"],
            "Default policy: refuse auto-execution for unknown commands.",
        ],
        evidence=[],
        verdict_generated_at=generated_at,
    )


def _verdict_from_match(
    *,
    tool_id: str,
    command: str,
    match: CommandMatch,
    generated_at: datetime,
) -> ValidateCommandResponse:
    claim = match.claim
    policy = _query_policy()

    # Re-classify to catch understated risk. The orchestrator already does
    # this at acceptance time; we repeat here as defence-in-depth in case a
    # claim was accepted under an earlier classifier and the rules have
    # tightened since.
    classified = classify_action(claim.subject, claim.statement, claim.tool_id)
    effective_risk = (
        classified.risk_level
        if RISK_ORDER[classified.risk_level] >= RISK_ORDER[claim.risk_level]
        else claim.risk_level
    )

    safety_rule = _safety_policy_by_risk()[effective_risk.value]
    auto_allowed_by_risk = bool(safety_rule["auto_execute_allowed"])
    requires_confirmation = bool(safety_rule["requires_human_confirmation"])

    min_confidence = float(policy["min_confidence_for_auto_execute"])
    min_level_value = policy["min_verification_level_for_auto_execute"]
    min_level_rank = VERIFICATION_LEVEL_ORDER[VerificationLevel(min_level_value)]

    confidence_gate_open = match.confidence >= min_confidence
    level_gate_open = (
        VERIFICATION_LEVEL_ORDER[match.verification_level] >= min_level_rank
    )

    safe_to_auto_execute = (
        auto_allowed_by_risk and confidence_gate_open and level_gate_open
    )

    reasons: list[str] = [
        f"Matched claim '{match.matched_subject}' by {match.match_method}.",
    ]
    # Surface the matched claim's verification status when it isn't
    # ACCEPTED. The matcher deliberately surfaces PENDING and
    # REQUIRES_HUMAN_REVIEW claims so the verdict can be informative
    # ("matched but partial confidence") rather than a misleading
    # default-deny. The confidence + level gates below independently
    # prevent these from driving auto-execution.
    if claim.verification_status != VerificationStatus.ACCEPTED:
        reasons.append(
            "Matched claim is at verification_status="
            f"'{claim.verification_status.value}' — the orchestrator has "
            "not fully accepted it; verdict is informational."
        )
    if RISK_ORDER[classified.risk_level] > RISK_ORDER[claim.risk_level]:
        reasons.append(
            f"Risk classifier upgraded risk from declared "
            f"'{claim.risk_level.value}' to '{classified.risk_level.value}'."
        )
    if not auto_allowed_by_risk:
        reasons.append(
            f"Safety policy blocks auto-execution at risk level "
            f"'{effective_risk.value}'."
        )
    if not confidence_gate_open:
        reasons.append(
            policy["low_confidence_reason"]
            + f" (confidence {match.confidence:.2f} < threshold {min_confidence:.2f})"
        )
    if not level_gate_open:
        reasons.append(
            policy["low_verification_reason"]
            + f" (level {match.verification_level.value} <"
            f" required {min_level_value})"
        )
    # Always carry the risk classifier's reasons; they explain WHY the
    # classifier picked the effective level.
    reasons.extend(classified.reasons)

    return ValidateCommandResponse(
        tool_id=tool_id,
        command=command,
        matched_claim_id=claim.claim_id,
        match_method=match.match_method,
        safe_to_auto_execute=safe_to_auto_execute,
        requires_human_confirmation=requires_confirmation or not safe_to_auto_execute,
        risk_level=effective_risk,
        verification_level=match.verification_level,
        confidence=match.confidence,
        confidence_band=band_for_score(match.confidence),
        reasons=reasons,
        evidence=[_project_evidence(e) for e in claim.evidence],
        verdict_generated_at=generated_at,
    )


def _project_evidence(evidence: Evidence) -> EvidenceCitation:
    return EvidenceCitation(
        evidence_type=evidence.evidence_type,
        source_uri=evidence.source_uri,
        trust_level=evidence.trust_level,
    )


def _dimensions_from_assessment(assessment: RiskAssessment) -> RiskDimensions:
    """Project the risk engine's `RiskDimension` enum set into the boolean
    dimension flags the explain_risk response exposes. The mapping is
    intentionally direct (one dimension → one boolean) so a future
    additions to `RiskDimension` surface as a missed mapping rather than a
    silent default."""
    dims = set(assessment.dimensions)
    destructive = RiskDimension.DESTRUCTIVE in dims
    remote_mutation = RiskDimension.REMOTE_MUTATION in dims
    return RiskDimensions(
        destructive_action=destructive,
        mutates_remote_state=remote_mutation or destructive,
        reversible=not destructive,
        requires_auth=RiskDimension.AUTH_SENSITIVE in dims,
        may_cost_money=RiskDimension.COST_INCURRING in dims,
        may_expose_secrets=RiskDimension.SECRET_EXPOSURE in dims,
    )


# -------- Pagination helper --------


def _paginate_all(
    fetch_page: Callable[[int, int], list],
    *,
    page_size: int,
    hard_cap: int,
) -> list:
    """Walk every page until the store is exhausted or `hard_cap` is hit.

    Mirrors the matcher's pagination contract: we use a hard cap to bound
    in-memory growth on a pathologically large store, and stop early when
    a page comes back shorter than `page_size` (last page)."""
    out: list = []
    offset = 0
    while len(out) < hard_cap:
        page = fetch_page(page_size, offset)
        if not page:
            break
        out.extend(page)
        offset += len(page)
        if len(page) < page_size:
            break
    return out


# -------- search_tools scoring + summary --------


def _score_tool_spec(spec: ToolSpec, query: str) -> tuple[int, str]:
    """Return (score, match_reason). Score 0 means no match.

    Empty query matches every spec at the lowest tier so the result is
    deterministic-but-uninformative ("list all"); the caller decides
    whether to keep that or skip."""
    if not query:
        return 1, "no-query (all tools)"
    tid = spec.tool_id.lower()
    if tid == query:
        return 100, "tool_id exact"
    if query in tid:
        return 80, f"tool_id substring '{query}'"
    if query in spec.name.lower():
        return 60, f"name substring '{query}'"
    for cap in spec.capabilities:
        if query in cap.lower():
            return 40, f"capability substring '{cap}'"
    return 0, ""


def _summarise_tool_spec(spec: ToolSpec, *, match_reason: str) -> ToolMatchSummary:
    """Build the compact summary returned by search_tools.

    `highest_risk_level` is the maximum risk across the spec's commands; if
    the spec has no commands (a capability-only spec), fall back to the
    risk_profile.default_risk_level."""
    if spec.commands:
        highest = max(
            spec.commands, key=lambda c: RISK_ORDER[c.risk_level]
        ).risk_level
    else:
        highest = spec.risk_profile.default_risk_level

    return ToolMatchSummary(
        tool_id=spec.tool_id,
        name=spec.name,
        interfaces=list(spec.interfaces),
        capability_count=len(spec.capabilities),
        verified_command_count=len(spec.commands),
        highest_risk_level=highest,
        verification_level=spec.verification_level,
        match_reason=match_reason,
    )


# -------- find_safe_workflows scoring + summary --------


def _score_workflow_spec(spec: WorkflowSpec, query: str) -> int:
    """Score a workflow's match against a goal query. 0 = no match."""
    if not query:
        return 1
    goal = (spec.goal or "").lower()
    if goal == query:
        return 100
    if query in goal:
        return 60
    # Token overlap: count goal-words present in query, scaled by tokens.
    if goal:
        goal_tokens = set(goal.split())
        query_tokens = set(query.split())
        overlap = len(goal_tokens & query_tokens)
        if overlap:
            return 20 + overlap * 5
    return 0


def _summarise_workflow_spec(spec: WorkflowSpec) -> WorkflowSummary:
    aggregate_risk = max(
        spec.steps, key=lambda step: RISK_ORDER[step.risk_level]
    ).risk_level
    requires_confirmation = any(step.requires_confirmation for step in spec.steps)
    return WorkflowSummary(
        workflow_id=spec.workflow_id,
        goal=spec.goal or "",
        tool_ids=list(spec.tool_ids),
        step_count=len(spec.steps),
        aggregate_risk_level=aggregate_risk,
        verification_level=spec.verification_level,
        requires_confirmation=requires_confirmation,
    )


__all__ = [
    "QueryEngine",
]
