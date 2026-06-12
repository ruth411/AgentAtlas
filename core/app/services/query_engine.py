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
import re
from typing import Any, Literal

from app.schemas.enums import (
    ClaimType,
    RISK_ORDER,
    VERIFICATION_LEVEL_ORDER,
    ConfidenceBand,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.schemas.claim import KnowledgeClaim
from app.schemas.query import (
    Answer,
    AskResponse,
    CapabilityRecord,
    ConstraintSetResponse,
    EvidenceCitation,
    ExplainRiskResponse,
    EffectProfileResponse,
    GetCapabilitiesResponse,
    RiskDimensions,
    ResolveActionResponse,
    ResolveSubjectResponse,
    SafeWorkflowResponse,
    SearchToolsResponse,
    SubjectSpecResponse,
    SubjectSummary,
    ToolMatchSummary,
    ValidateCommandResponse,
    WorkflowPlanResponse,
    WorkflowPlanSummary,
    WorkflowSummary,
)
from app.schemas.risk import RiskAssessment, RiskDimension
from app.schemas.verification import VerificationResult
from app.schemas.tool_spec import ToolSpec
from app.schemas.workflow_spec import WorkflowSpec
from app.services.claim_store import ClaimStore
from app.services.command_matcher import CommandMatch, match_command
from app.services.confidence_scorer import band_for_score
from app.services.contract_paths import contract_path
from app.services.embedding_service import (
    EmbeddingService,
    cosine_similarity,
    deserialize_embedding,
)
from app.services.risk_classifier import classify_action


_QUERY_POLICY_CONTRACT = contract_path("query_policy.v1.json")
_STAGE_0_CONTRACT = contract_path("ayiru_stage_0.v1.json")


# Stage 17 — ask() lexical retrieval knobs.
#
# Token-overlap matching on (subject, statement, tool_id). Stop-words are
# the closed list of English function words that show up in every natural-
# language question ("how do I"). Removing them lets the matcher rank
# claims on the content tokens that actually distinguish them.
#
# Critical: do NOT impose a min-length filter on top of the stop-word set.
# Half the canonical Unix CLI vocabulary is ≤ 2 chars — `rm`, `ls`, `cd`,
# `mv`, `cp`, `du`, `df`, `ps`, plus modern dev tools `gh`, `jq`, `yq`,
# `bq`, `pg`, `vi`. A length filter at 3 silently makes those commands
# invisible to ask(). The audit caught this; the regression test is at
# tests/test_query_ask.py::test_short_command_tokens_survive_tokenization.
#
# _ASK_SCORE_THRESHOLD is the floor below which the engine reports
# `fallback_recommended=True` even if some lexical overlap exists. The
# value (0.30) was picked so that a question whose only matching term is
# the tool_id itself ("how to use docker?") falls through to web_search
# rather than returning a random docker claim — but a question that
# matches subject AND a content token in the statement clears the bar.
# Tune from real audit data once Stage 18 telemetry lands.
_ASK_SCORE_THRESHOLD = 0.30

# Stage 22-stretch — semantic re-rank weighting.
#
# When a claim has a stored embedding, ask() blends lexical and semantic
# scores into a hybrid:
#   hybrid = (1 - _SEMANTIC_WEIGHT) * lexical + _SEMANTIC_WEIGHT * semantic
# Semantic dominates because the failure mode we're fixing is "right
# claim found by lexical, but at 0.45 confidence — below the SDK's
# is_useful 0.6 gate." Cosine similarity on correct matches clusters
# 0.7-0.85, so with semantic weighted 0.7 the hybrid clears 0.6 for
# real matches while keeping the threshold honest for noise.
#
# Set _SEMANTIC_WEIGHT=0 to disable semantic re-rank entirely (back to
# the v0.2 lexical-only behavior — useful for A/B comparisons).
_SEMANTIC_WEIGHT = 0.70
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "about", "after", "again", "all", "also", "am", "an", "and", "any",
    "are", "as", "at", "be", "because", "been", "before", "being", "between",
    "both", "but", "by", "can", "could", "did", "do", "does", "doing", "for",
    "from", "had", "has", "have", "having", "he", "her", "here", "hers",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "let", "let's", "lets", "like", "me", "my", "no", "nor", "not", "now",
    "of", "off", "on", "once", "only", "or", "other", "our", "ours", "out",
    "over", "own", "please", "same", "she", "should", "show", "so", "some",
    "such", "tell", "than", "that", "the", "their", "theirs", "them",
    "then", "there", "these", "they", "this", "those", "through", "to",
    "too", "under", "until", "up", "use", "using", "very", "want", "was",
    "way", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "will", "with", "would", "yes", "you", "your", "yours",
})

# Stage 17 — cost-savings heuristic.
#
# Tokens an agent saves when it picks ask() over web_search. A typical
# Claude/OpenAI web_search call costs ~30 input tokens (the query) +
# ~800 output tokens (the search-result digest) ≈ 830 tokens. Ayiru
# returns the same answer in ~150 response tokens; net saving ≈ 680
# tokens per query. The constant is the only knob; do not scatter
# token-cost arithmetic across the codebase. Recalibrate from observed
# audit data once Stage 18 telemetry lands (the calibration task in
# plan_v02.md §Stage 18.3 makes the constant a measured number).
_AVERAGE_WEB_SEARCH_TOKENS = 830
_INFORMATIONAL_SURFACES: frozenset[str] = frozenset({"recipes", "errors"})
_TROUBLESHOOTING_TOKENS: frozenset[str] = frozenset(
    {
        "debug",
        "diagnose",
        "error",
        "errors",
        "failed",
        "failing",
        "failure",
        "fix",
        "resolve",
        "stacktrace",
        "timeout",
        "traceback",
        "troubleshoot",
    }
)
_WORKFLOW_TOKENS: frozenset[str] = frozenset(
    {
        "cookbook",
        "example",
        "examples",
        "flow",
        "procedure",
        "recipe",
        "recipes",
        "steps",
        "walkthrough",
        "workflow",
    }
)


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

    def resolve_subject(
        self,
        *,
        subject_hint: str,
        kind: str | None = None,
        family_hint: str | None = None,
        limit: int = 20,
    ) -> ResolveSubjectResponse:
        query = subject_hint.strip()
        if family_hint:
            query = family_hint.strip()
        normalized_query = query.lower()
        scored_matches: list[tuple[int, SubjectSummary]] = []

        if kind in {None, "tool", "api", "sdk", "adk", "subject"}:
            tool_matches = self.search_tools(query=query, limit=limit, offset=0)
            for item in tool_matches.matches:
                spec = self._store.get_canonical_tool_spec(item.tool_id)
                if spec is None:
                    continue
                summary = _tool_spec_to_subject_summary(spec, match_reason=item.match_reason)
                if kind is not None and summary.subject_kind != kind:
                    continue
                score, _ = _score_tool_spec(spec, normalized_query)
                scored_matches.append((score, summary))

        if kind in {None, "workflow", "subject"}:
            workflow_specs = _paginate_all(
                lambda lim, off: self._store.list_canonical_workflow_specs(
                    limit=lim,
                    offset=off,
                ),
                page_size=100,
                hard_cap=2_000,
            )
            for spec in workflow_specs:
                score = _score_workflow_spec(spec, normalized_query)
                if score <= 0:
                    continue
                scored_matches.append(
                    (
                        score,
                        _workflow_spec_to_subject_summary(
                            spec,
                            match_reason=_workflow_match_reason(spec, normalized_query),
                        ),
                    )
                )

        scored_matches.sort(
            key=lambda item: (
                -item[0],
                item[1].subject_kind != "tool",
                item[1].subject_id,
            )
        )
        matches = [summary for _, summary in scored_matches[:limit]]
        return ResolveSubjectResponse(
            subject_hint=subject_hint,
            kind=kind,  # type: ignore[arg-type]
            matches=matches,
            total=len(matches),
            limit=limit,
        )

    def get_subject_spec(self, *, subject_id: str) -> SubjectSpecResponse | None:
        tool_spec = self._store.get_canonical_tool_spec(subject_id.strip())
        if tool_spec is not None:
            return _tool_spec_to_subject_spec(tool_spec)
        workflow_spec = self._store.get_canonical_workflow_spec(subject_id.strip())
        if workflow_spec is not None:
            return _workflow_spec_to_subject_spec(workflow_spec)
        return None

    def get_capabilities(
        self,
        *,
        subject_id: str,
        capability_types: list[str] | None = None,
        accepted_only: bool = True,
        verification_min: VerificationLevel | None = None,
        limit: int = 50,
    ) -> GetCapabilitiesResponse:
        capability_types = capability_types or []
        claims = self._claims_for_subject(subject_id)
        if not claims:
            return GetCapabilitiesResponse(
                subject_id=subject_id,
                accepted_only=accepted_only,
                capabilities=[],
                total=0,
                limit=limit,
            )
        latest_results = self._store.get_latest_verification_results(
            [claim.claim_id for claim in claims]
        )
        capabilities: list[CapabilityRecord] = []
        for claim in claims:
            if claim.verification_status in {
                VerificationStatus.REJECTED,
                VerificationStatus.CONFLICT_DETECTED,
            }:
                continue
            if accepted_only and claim.verification_status != VerificationStatus.ACCEPTED:
                continue
            capability = _claim_to_capability_record(
                claim,
                latest_result=latest_results.get(claim.claim_id),
            )
            if capability_types and capability.capability_type not in capability_types:
                continue
            if (
                verification_min is not None
                and VERIFICATION_LEVEL_ORDER[capability.verification_level]
                < VERIFICATION_LEVEL_ORDER[verification_min]
            ):
                continue
            capabilities.append(capability)
        capabilities.sort(
            key=lambda item: (
                item.verification_status != VerificationStatus.ACCEPTED,
                -VERIFICATION_LEVEL_ORDER[item.verification_level],
                -item.confidence,
                item.capability_id,
            )
        )
        page = capabilities[:limit]
        return GetCapabilitiesResponse(
            subject_id=subject_id,
            accepted_only=accepted_only,
            capabilities=page,
            total=len(capabilities),
            limit=limit,
        )

    def get_constraints(
        self,
        *,
        subject_id: str,
        action_intent: str | None = None,
        accepted_only: bool = True,
    ) -> ConstraintSetResponse:
        response = self.get_capabilities(
            subject_id=subject_id,
            capability_types=["constraint", "environment"],
            accepted_only=accepted_only,
            limit=200,
        )
        return ConstraintSetResponse(
            subject_id=subject_id,
            action_intent=action_intent,
            constraints=response.capabilities,
            total=response.total,
        )

    def get_effects(
        self,
        *,
        subject_id: str,
        action_intent: str | None = None,
        accepted_only: bool = True,
    ) -> EffectProfileResponse:
        response = self.get_capabilities(
            subject_id=subject_id,
            capability_types=["effect", "deprecation"],
            accepted_only=accepted_only,
            limit=200,
        )
        aggregate = None
        if response.capabilities:
            aggregate = max(
                (cap.risk_level or RiskLevel.NONE for cap in response.capabilities),
                key=lambda level: RISK_ORDER[level],
            )
        return EffectProfileResponse(
            subject_id=subject_id,
            action_intent=action_intent,
            effects=response.capabilities,
            total=response.total,
            aggregate_risk_level=aggregate,
            requires_confirmation=aggregate in {RiskLevel.HIGH, RiskLevel.CRITICAL},
        )

    def resolve_action(
        self,
        *,
        subject_id: str,
        action_intent: str,
        command: str | None = None,
        environment: str | None = None,
        accepted_only: bool = True,
        limit: int = 5,
    ) -> ResolveActionResponse:
        constraints = self.get_constraints(
            subject_id=subject_id,
            action_intent=action_intent,
            accepted_only=accepted_only,
        )
        effects = self.get_effects(
            subject_id=subject_id,
            action_intent=action_intent,
            accepted_only=accepted_only,
        )
        if command:
            verdict = self.validate_command(tool_id=subject_id, command=command)
            top_capability = None
            if verdict.matched_claim_id:
                claim = self._store.get(verdict.matched_claim_id)
                if claim is not None:
                    latest = self._store.get_latest_verification_result(claim.claim_id)
                    top_capability = _claim_to_capability_record(
                        claim,
                        latest_result=latest,
                    )
            supporting = [top_capability] if top_capability is not None else []
            return ResolveActionResponse(
                subject_id=subject_id,
                action_intent=action_intent,
                command=command,
                environment=environment,
                resolution_mode="command_match",
                top_capability=top_capability,
                supporting_capabilities=supporting,
                constraints=constraints.constraints,
                effects=effects.effects,
                fallback_recommended=verdict.match_method == "none",
                safe_to_auto_execute=verdict.safe_to_auto_execute,
                requires_human_confirmation=verdict.requires_human_confirmation,
                risk_level=verdict.risk_level,
                verification_status=top_capability.verification_status if top_capability else None,
                verification_level=verdict.verification_level,
                confidence=verdict.confidence,
                confidence_band=verdict.confidence_band,
                reasons=list(verdict.reasons),
            )

        ranked = self._rank_capabilities_for_action(
            subject_id=subject_id,
            query=action_intent,
            accepted_only=accepted_only,
            limit=limit,
        )
        top = ranked[0] if ranked else None
        fallback = top is None or top.confidence < _ASK_SCORE_THRESHOLD
        return ResolveActionResponse(
            subject_id=subject_id,
            action_intent=action_intent,
            command=None,
            environment=environment,
            resolution_mode="capability_search",
            top_capability=top,
            supporting_capabilities=ranked,
            constraints=constraints.constraints,
            effects=effects.effects,
            fallback_recommended=fallback,
            safe_to_auto_execute=None,
            requires_human_confirmation=(
                None if top is None else top.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            ),
            risk_level=top.risk_level if top else None,
            verification_status=top.verification_status if top else None,
            verification_level=top.verification_level if top else None,
            confidence=top.confidence if top else 0.0,
            confidence_band=top.confidence_band if top else ConfidenceBand.NONE,
            reasons=[top.relevance_reason] if top and top.relevance_reason else [],
        )

    def get_workflow_plan(
        self,
        *,
        goal: str,
        environment: str | None = None,
        limit: int = 20,
    ) -> WorkflowPlanResponse:
        response = self.find_safe_workflows(goal=goal, environment=environment, limit=limit)
        return WorkflowPlanResponse(
            goal=goal,
            environment=environment,
            plans=[
                WorkflowPlanSummary(
                    workflow_id=match.workflow_id,
                    goal=match.goal,
                    subject_ids=match.tool_ids,
                    step_count=match.step_count,
                    aggregate_risk_level=match.aggregate_risk_level,
                    verification_level=match.verification_level,
                    requires_confirmation=match.requires_confirmation,
                )
                for match in response.matches
            ],
            total=response.total,
        )

    # -------- ask --------

    def ask(
        self,
        *,
        question: str,
        limit: int = 5,
        tool_id_hint: str | None = None,
    ) -> AskResponse:
        """Stage 17 — the headline v0.2 retrieval surface.

        Lexical token-overlap ranking against the local graph. The
        engine serves two answer tiers:

        - accepted: verification-accepted claims
        - informational: cited claims still pending review

        Accepted answers win by default. Informational answers are a
        fallback tier for troubleshooting / workflow guidance or for
        cases where no accepted claim clears the quality bar.

        Returns the same shape on hit OR miss. On miss, `answers=[]`
        and `fallback_recommended=True` — the agent's signal to escalate
        to web_search. On a confident hit, `fallback_recommended=False`.

        `estimated_tokens_saved` is heuristic — see
        `_AVERAGE_WEB_SEARCH_TOKENS`. Stage 18 wires this into the
        audit log; until then it's an informational hint to the caller.
        """
        from app.services.ids import generate_query_id

        normalized_question = question.strip()
        question_tokens = _tokenize_question(normalized_question)
        generated_at = self._timestamp()
        # Mint one query id per ask() call so the audit row + the
        # caller-side debug log can correlate the same event.
        query_id = generate_query_id()

        # Pure stop-word query (or empty after stripping): no signal to rank
        # on. Return an honest fallback rather than degenerate ranking.
        if not question_tokens:
            self._emit_query_served(
                query_id=query_id,
                question_length=len(normalized_question),
                answers_returned=0,
                tokens_saved=0,
                top_claim_id=None,
                answer_status="miss",
                fallback_recommended=True,
                tool_id_hint=tool_id_hint,
                fallback_reason="all_stopwords",
            )
            return AskResponse(
                question=normalized_question,
                answers=[],
                answer_status="miss",
                fallback_recommended=True,
                estimated_tokens_saved=0,
                generated_at=generated_at,
            )

        if limit < 1:
            limit = 1
        if limit > 20:
            limit = 20

        # Pull every candidate claim (optionally narrowed by tool_id_hint).
        # The v0.2 graph holds a few thousand claims; in-memory
        # ranking is comfortably under 100ms for that scale. Embeddings +
        # SQL pre-filter land in v0.2.x stretch / v0.3.
        normalized_hint: str | None = None
        if tool_id_hint is not None:
            normalized_hint = tool_id_hint.strip().lower() or None

        # Resolve coarse hints (e.g. "ffmpeg") to the surface-decomposed
        # tool_ids that actually exist in the graph (ffmpeg-cli /
        # ffmpeg-recipes / …). Returns the original hint unchanged when
        # it matches an existing tool_id exactly, or the empty list when
        # nothing matches at all (preserves the "honest miss" path).
        effective_tool_ids = self._resolve_tool_id_hint(normalized_hint)

        # Stage 19: pull both ACCEPTED (curated, full-orchestrator-blessed)
        # claims AND PENDING claims (uncurated L0 state — Stage 20's bulk
        # ingest lands here). REJECTED / CONFLICT_DETECTED claims are
        # excluded downstream via the latest-verification lookup so the
        # matcher never surfaces "the orchestrator said no" content.
        # validate_command stays strict — it requires L2+ via command_matcher.
        if effective_tool_ids is None:
            # No hint — fetch everything.
            candidates = _paginate_all(
                lambda lim, off: self._store.list(
                    tool_id=None,
                    limit=lim,
                    offset=off,
                ),
                page_size=int(_query_policy()["default_search_limit"]),
                hard_cap=10_000,
            )
        elif len(effective_tool_ids) == 0:
            # Hint given but matched nothing — force-miss. Don't widen
            # to the whole graph; the caller explicitly asked for a
            # tool that doesn't exist.
            candidates = []
        elif len(effective_tool_ids) == 1:
            # Exact tool_id match — push the filter into the SQL layer.
            candidates = _paginate_all(
                lambda lim, off: self._store.list(
                    tool_id=effective_tool_ids[0],
                    limit=lim,
                    offset=off,
                ),
                page_size=int(_query_policy()["default_search_limit"]),
                hard_cap=10_000,
            )
        else:
            # Multi-surface expansion — union of all surfaces for the
            # given family. Fetch each separately and concatenate; per-
            # surface counts are small (typically 5–50 claims) so this
            # stays well under the 10k hard cap.
            candidates = []
            for surface_id in effective_tool_ids:
                candidates.extend(
                    _paginate_all(
                        lambda lim, off, sid=surface_id: self._store.list(
                            tool_id=sid,
                            limit=lim,
                            offset=off,
                        ),
                        page_size=int(_query_policy()["default_search_limit"]),
                        hard_cap=10_000,
                    )
                )

        # Uncurated tools (not in the v2 contract's curated set) surface
        # with a marker in match_reason so the caller can tell graph-
        # blessed answers apart from L0 contributions.
        from app.services.claim_store import _curated_tool_ids
        curated_set = _curated_tool_ids()

        # Never surface claims whose orchestrator decision explicitly
        # distrusts them.
        excluded_statuses = {
            VerificationStatus.REJECTED,
            VerificationStatus.CONFLICT_DETECTED,
        }

        scored: list[tuple[float, str, KnowledgeClaim, str]] = []
        for claim in candidates:
            if claim.verification_status in excluded_statuses:
                continue
            score, reason = _score_claim_for_question(claim, question_tokens)
            if score <= 0.0:
                continue
            if claim.tool_id not in curated_set:
                reason = f"{reason}; uncurated tool ({claim.tool_id})"
            biased_score, bias_reason = _apply_ask_bias(
                claim,
                base_score=score,
                question_tokens=question_tokens,
            )
            if biased_score <= 0.0:
                continue
            if bias_reason:
                reason = f"{reason}; {bias_reason}"
            scored.append((biased_score, claim.claim_id, claim, reason))

        # Stage 22-stretch — semantic re-rank.
        # For each lexically-scored candidate that has a stored embedding,
        # replace its score with a hybrid of lexical + cosine-similarity.
        # Candidates without an embedding (un-reindexed legacy rows) keep
        # their lexical score so the engine still works gracefully.
        scored = self._semantic_rerank(normalized_question, scored)

        # Sort: score DESC, claim_id ASC (deterministic tie-break).
        scored.sort(key=lambda row: (-row[0], row[1]))

        accepted_scored = [
            row for row in scored
            if row[2].verification_status == VerificationStatus.ACCEPTED
        ]
        informational_scored = [
            row for row in scored
            if row[2].verification_status in {
                VerificationStatus.PENDING,
                VerificationStatus.REQUIRES_HUMAN_REVIEW,
            }
        ]
        accepted_top = accepted_scored[:limit]
        informational_top = informational_scored[:limit]
        accepted_score = accepted_top[0][0] if accepted_top else 0.0
        informational_score = informational_top[0][0] if informational_top else 0.0

        top: list[tuple[float, str, KnowledgeClaim, str]] = []
        answer_status: Literal["accepted", "informational", "miss"] = "miss"
        fallback = True
        if (
            _question_prefers_informational_surface(question_tokens)
            and informational_score >= _ASK_SCORE_THRESHOLD
            and informational_score >= accepted_score + 0.05
        ):
            top = informational_top
            answer_status = "informational"
            fallback = False
        elif accepted_score >= _ASK_SCORE_THRESHOLD:
            top = accepted_top
            answer_status = "accepted"
            fallback = False
        elif informational_score >= _ASK_SCORE_THRESHOLD:
            top = informational_top
            answer_status = "informational"
            fallback = False

        answers: list[Answer] = []
        if not fallback:
            # Single batch lookup for verification levels on the top-N
            # claims — keeps ask() at one extra query no matter how many
            # candidates were scored.
            top_claim_ids = [claim.claim_id for _, _, claim, _ in top]
            latest_results = self._store.get_latest_verification_results(top_claim_ids)
            answers = [
                _claim_to_answer(
                    claim,
                    match_reason=reason,
                    latest_result=latest_results.get(claim.claim_id),
                    match_score=match_score,
                )
                for match_score, _, claim, reason in top
            ]

        # Heuristic token savings. Zero on fallback so the caller doesn't
        # get a positive savings count for a miss they'll re-route anyway.
        if answers:
            response_tokens = len(
                json.dumps([a.model_dump(mode="json") for a in answers])
            ) // 4
            estimated_savings = max(0, _AVERAGE_WEB_SEARCH_TOKENS - response_tokens)
        else:
            estimated_savings = 0

        top_claim_id = answers[0].claim_id if answers else None
        self._emit_query_served(
            query_id=query_id,
            question_length=len(normalized_question),
            answers_returned=len(answers),
            tokens_saved=estimated_savings,
            top_claim_id=top_claim_id,
            answer_status=answer_status,
            fallback_recommended=fallback,
            tool_id_hint=tool_id_hint,
            fallback_reason=("below_threshold" if fallback else None),
        )

        return AskResponse(
            question=normalized_question,
            answers=answers,
            answer_status=answer_status,
            fallback_recommended=fallback,
            estimated_tokens_saved=estimated_savings,
            generated_at=generated_at,
        )

    def _resolve_tool_id_hint(
        self,
        normalized_hint: str | None,
    ) -> list[str] | None:
        """Resolve a coarse ``tool_id_hint`` to the surface tool_ids that
        actually exist in the graph.

        The Stage 20 five-surface decomposition split monolithic tools
        like ``ffmpeg`` into ``ffmpeg-cli``, ``ffmpeg-config``,
        ``ffmpeg-filters``, ``ffmpeg-recipes``, ``ffmpeg-errors``. MCP
        clients (and humans) naturally hint with the family name; before
        this expansion the matcher would filter to zero claims and miss.

        Return value semantics (four distinct states):
          * ``None`` — no hint given. Caller should fetch unfiltered.
          * ``[]`` — hint was given but matched no existing tool_id
            and no surface prefix. Caller must force a miss: an
            explicit hint at an unknown tool should not silently widen
            to the whole graph.
          * ``[exact_id]`` — the hint matched an existing tool_id
            directly. Caller pushes the single-id filter into SQL.
          * ``[id1, id2, ...]`` — the hint was a family prefix that
            expanded to multiple surfaces. Caller unions the
            per-surface candidate lists.

        Resolution order:
          1. Exact match on an existing tool_id → ``[hint]``.
          2. Prefix match of ``<hint>-*`` against existing tool_ids →
             the matched surface list.
          3. No match → ``[]`` (force-miss).
        """
        if normalized_hint is None:
            return None
        existing = set(self._store.list_distinct_tool_ids())
        if normalized_hint in existing:
            return [normalized_hint]
        prefix = f"{normalized_hint}-"
        matching = sorted(t for t in existing if t.startswith(prefix))
        if matching:
            return matching
        # Hint given but no exact or prefix match — honest miss.
        return []

    def _claims_for_subject(self, subject_id: str) -> list[KnowledgeClaim]:
        normalized = subject_id.strip().lower()
        effective_tool_ids = self._resolve_tool_id_hint(normalized)
        if effective_tool_ids is None:
            return []
        if len(effective_tool_ids) == 0:
            return []
        claims: list[KnowledgeClaim] = []
        for tool_id in effective_tool_ids:
            claims.extend(
                _paginate_all(
                    lambda lim, off, tid=tool_id: self._store.list(
                        tool_id=tid,
                        limit=lim,
                        offset=off,
                    ),
                    page_size=int(_query_policy()["default_search_limit"]),
                    hard_cap=10_000,
                )
            )
        return claims

    def _rank_capabilities_for_action(
        self,
        *,
        subject_id: str,
        query: str,
        accepted_only: bool,
        limit: int,
    ) -> list[CapabilityRecord]:
        question_tokens = _tokenize_question(query.strip())
        claims = self._claims_for_subject(subject_id)
        latest_results = self._store.get_latest_verification_results(
            [claim.claim_id for claim in claims]
        )
        scored: list[tuple[float, str, KnowledgeClaim, str]] = []
        for claim in claims:
            if claim.verification_status in {
                VerificationStatus.REJECTED,
                VerificationStatus.CONFLICT_DETECTED,
            }:
                continue
            if accepted_only and claim.verification_status != VerificationStatus.ACCEPTED:
                continue
            score, reason = _score_claim_for_question(claim, question_tokens)
            if score <= 0.0:
                continue
            biased_score, bias_reason = _apply_ask_bias(
                claim,
                base_score=score,
                question_tokens=question_tokens,
            )
            if bias_reason:
                reason = f"{reason}; {bias_reason}"
            scored.append((biased_score, claim.claim_id, claim, reason))
        scored = self._semantic_rerank(query, scored)
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [
            _claim_to_capability_record(
                claim,
                latest_result=latest_results.get(claim.claim_id),
                relevance_reason=reason,
                match_score=match_score,
            )
            for match_score, _, claim, reason in scored[:limit]
        ]

    def _semantic_rerank(
        self,
        question: str,
        scored: list[tuple[float, str, KnowledgeClaim, str]],
    ) -> list[tuple[float, str, KnowledgeClaim, str]]:
        """Stage 22-stretch — re-rank lexical candidates by semantic
        similarity to the question.

        Strategy:
          1. Pull each candidate's stored embedding (NULL = no rerank for
             that row; keeps lexical score).
          2. Embed the question once via the shared EmbeddingService
             singleton. Model loads lazily on first call.
          3. For each candidate with an embedding, replace its score with
             ``(1 - W) * lexical + W * cosine`` where W = _SEMANTIC_WEIGHT.

        Returns a new list with updated scores; caller still sorts.

        If the EmbeddingService can't be loaded (offline, missing model
        weights), this falls back gracefully to lexical-only ranking
        rather than failing the entire ``ask()`` call."""

        if _SEMANTIC_WEIGHT <= 0.0 or not scored:
            return scored

        # Pull only the embeddings we need (top-N candidates after lexical
        # — typically <50 even for popular tools).
        candidate_ids = [row[1] for row in scored]
        embeddings: dict[str, list[float]] = {}
        for cid in candidate_ids:
            raw = self._store.get_embedding(cid)
            decoded = deserialize_embedding(raw)
            if decoded is not None:
                embeddings[cid] = decoded

        if not embeddings:
            # No candidate has an embedding yet → behave exactly like the
            # v0.2 lexical-only engine. The `ayiru reindex` command is
            # the one-shot fix.
            return scored

        try:
            question_vec = EmbeddingService.get_default().embed_one(question)
        except Exception:  # noqa: BLE001
            # Model load failed (e.g., no network, no cached weights).
            # Fall back to lexical; the operator can fix this offline
            # without breaking the ask() endpoint.
            return scored

        reranked: list[tuple[float, str, KnowledgeClaim, str]] = []
        for lex_score, cid, claim, reason in scored:
            emb = embeddings.get(cid)
            if emb is None:
                reranked.append((lex_score, cid, claim, reason))
                continue
            sem = max(0.0, cosine_similarity(question_vec, emb))
            hybrid = (1 - _SEMANTIC_WEIGHT) * lex_score + _SEMANTIC_WEIGHT * sem
            new_reason = f"{reason}; semantic={sem:.2f}, hybrid={hybrid:.2f}"
            reranked.append((hybrid, cid, claim, new_reason))
        return reranked

    def _emit_query_served(
        self,
        *,
        query_id: str,
        question_length: int,
        answers_returned: int,
        tokens_saved: int,
        top_claim_id: str | None,
        answer_status: str,
        fallback_recommended: bool,
        tool_id_hint: str | None,
        fallback_reason: str | None,
    ) -> None:
        """Stage 18 — single emission point for the QUERY_SERVED audit row.

        Centralised here (not inline at every return point) so the
        contract for what we record is testable and self-documenting:

        - Raw question text is NEVER persisted — only its length.
          Questions can contain proprietary code or PII; we keep the
          forensic signal (length + match + savings) without the leak.
        - On a fallback, ``top_claim_id`` is None and ``tokens_saved``
          is 0 — matching the response shape so the aggregator at
          /v1/stats/savings doesn't double-count.
        - ``fallback_reason`` distinguishes "stopword-only question",
          "score below threshold", and (later) "empty graph" so the
          telemetry tells us *why* the matcher missed.

        Defense in depth: even though ``ClaimStore.record_query_served``
        already swallows its own errors, we wrap the call here too —
        a future store implementation that forgets the try/except must
        never break the ask() response. Telemetry is best-effort by
        contract; the read path is the user-facing surface.
        """
        try:
            self._store.record_query_served(
                query_id=query_id,
                actor="agent",
                details={
                    "question_length": question_length,
                    "answers_returned": answers_returned,
                    "tokens_saved": tokens_saved,
                    "top_claim_id": top_claim_id,
                    "answer_status": answer_status,
                    "fallback_recommended": fallback_recommended,
                    "tool_id_hint": tool_id_hint,
                    "fallback_reason": fallback_reason,
                },
            )
        except Exception:
            import logging

            logging.getLogger("ayiru.telemetry").warning(
                "QUERY_SERVED audit emission failed at engine layer",
                exc_info=True,
                extra={"query_id": query_id},
            )

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
        fetched_at=evidence.captured_at,
    )


def _claim_type_to_capability_type(claim_type: ClaimType) -> str:
    return {
        ClaimType.TOOL_EXISTS: "existence",
        ClaimType.CLI_COMMAND_EXISTS: "invocation",
        ClaimType.CLI_FLAG_EXISTS: "invocation",
        ClaimType.API_ENDPOINT_EXISTS: "invocation",
        ClaimType.MCP_TOOL_EXISTS: "invocation",
        ClaimType.CONFIG_FIELD_EXISTS: "configuration",
        ClaimType.AUTH_REQUIREMENT: "constraint",
        ClaimType.ENVIRONMENT_REQUIREMENT: "environment",
        ClaimType.SIDE_EFFECT: "effect",
        ClaimType.DESTRUCTIVE_ACTION: "effect",
        ClaimType.FEATURE_DEPRECATED: "deprecation",
        ClaimType.WORKFLOW_STEP: "workflow",
    }.get(claim_type, "metadata")


def _claim_to_capability_record(
    claim: KnowledgeClaim,
    *,
    latest_result: VerificationResult | None,
    relevance_reason: str | None = None,
    match_score: float | None = None,
) -> CapabilityRecord:
    if latest_result is not None:
        verification_level = latest_result.verification_level
        confidence = latest_result.confidence
        confidence_band = latest_result.confidence_band
    else:
        verification_level = VerificationLevel.L1_SCHEMA_VALID
        confidence = claim.confidence or 0.0
        confidence_band = claim.confidence_band or band_for_score(confidence)
    if match_score is not None:
        confidence = max(0.0, min(1.0, match_score))
        confidence_band = band_for_score(confidence)
    return CapabilityRecord(
        capability_id=claim.claim_id,
        subject_id=claim.tool_id,
        capability_type=_claim_type_to_capability_type(claim.claim_type),  # type: ignore[arg-type]
        claim_type=claim.claim_type,
        title=claim.subject,
        detail=claim.statement,
        verification_status=claim.verification_status,
        verification_level=verification_level,
        confidence=confidence,
        confidence_band=confidence_band,
        risk_level=claim.risk_level,
        evidence=[_project_evidence(e) for e in claim.evidence],
        relevance_reason=relevance_reason,
    )


def _subject_kind_for_tool_spec(spec: ToolSpec) -> str:
    interfaces = {item.lower() for item in spec.interfaces}
    if any(item in interfaces for item in {"rest", "openapi", "graphql", "api"}):
        return "api"
    if any("sdk" in item for item in interfaces):
        return "sdk"
    if "adk" in spec.tool_id.lower() or "adk" in spec.name.lower():
        return "adk"
    return "tool"


def _tool_spec_to_subject_summary(spec: ToolSpec, *, match_reason: str) -> SubjectSummary:
    family = spec.tool_id.split("-", 1)[0] if "-" in spec.tool_id else spec.tool_id
    return SubjectSummary(
        subject_id=spec.tool_id,
        subject_kind=_subject_kind_for_tool_spec(spec),  # type: ignore[arg-type]
        name=spec.name,
        family=family,
        interfaces=list(spec.interfaces),
        capability_count=len(spec.capabilities),
        verification_level=spec.verification_level,
        match_reason=match_reason,
    )


def _workflow_spec_to_subject_summary(
    spec: WorkflowSpec, *, match_reason: str
) -> SubjectSummary:
    aggregate_risk = max(
        spec.steps, key=lambda step: RISK_ORDER[step.risk_level]
    ).risk_level
    return SubjectSummary(
        subject_id=spec.workflow_id,
        subject_kind="workflow",
        name=spec.goal or spec.workflow_id,
        family="workflow",
        interfaces=["workflow"],
        capability_count=len(spec.steps),
        verification_level=spec.verification_level,
        match_reason=match_reason or "workflow goal match",
    )


def _tool_spec_to_subject_spec(spec: ToolSpec) -> SubjectSpecResponse:
    family = spec.tool_id.split("-", 1)[0] if "-" in spec.tool_id else spec.tool_id
    return SubjectSpecResponse(
        subject_id=spec.tool_id,
        subject_kind=_subject_kind_for_tool_spec(spec),  # type: ignore[arg-type]
        name=spec.name,
        family=family,
        interfaces=list(spec.interfaces),
        capabilities=list(spec.capabilities),
        workflows=list(spec.workflows),
        verification_level=spec.verification_level,
        provenance_claim_ids=list(spec.provenance.source_claim_ids),
        provenance_evidence_ids=list(spec.provenance.source_evidence_ids),
    )


def _workflow_spec_to_subject_spec(spec: WorkflowSpec) -> SubjectSpecResponse:
    return SubjectSpecResponse(
        subject_id=spec.workflow_id,
        subject_kind="workflow",
        name=spec.goal or spec.workflow_id,
        family=spec.workflow_id,
        interfaces=[],
        capabilities=[step.action for step in spec.steps],
        workflows=[spec.workflow_id],
        verification_level=spec.verification_level,
        provenance_claim_ids=list(spec.provenance.source_claim_ids),
        provenance_evidence_ids=list(spec.provenance.source_evidence_ids),
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


# -------- ask scoring + summary --------


def _tokenize_question(question: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, drop stop-words.

    Returns the list of content tokens the matcher should rank on. An
    all-stop-word question (``"how do I"``) returns ``[]`` — the caller
    treats that as an automatic fallback rather than degenerate ranking
    against an empty filter.

    Critical: short tokens (1–2 chars) are NOT filtered. Half the Unix
    CLI vocabulary is ≤ 2 chars (`rm`, `ls`, `cd`, `gh`, `jq`, ...);
    suppressing them silently breaks the headline use case. The
    stop-word set already drops short noise particles (`a`, `i`, `is`,
    `to`, `of`, `do`).
    """
    raw = re.findall(r"[A-Za-z0-9]+", question.lower())
    return [token for token in raw if token not in _STOP_WORDS]


def _score_claim_for_question(
    claim: KnowledgeClaim, question_tokens: list[str]
) -> tuple[float, str]:
    """Return (score, match_reason) for one claim against the question.

    Score is a 0..1 float (typically — can exceed 1.0 on a heavily-matched
    statement, but normalised by the question-token count to stay
    comparable across queries of different length). Higher = more
    relevant. 0.0 means the candidate had zero overlap and should be
    dropped before sorting.

    Weights (chosen to dominate when the question's content tokens land
    in the subject, not just the statement prose):
      - subject token match  → weight 3.0
      - tool_id token match  → weight 2.0  (a question naming the tool is
                                a strong signal even if the rest is fuzzy)
      - statement token match → weight 1.0

    Confidence and recency are NOT in the score — they're orthogonal
    quality signals carried on the Answer payload for the caller to read.
    Boosting by confidence would let a low-relevance but high-confidence
    claim drown out a precisely-matched lower-confidence one, which is
    the wrong UX for a search box.

    `match_reason` is a one-line debug string surfaced to the caller via
    `Answer.match_reason`. Examples:
      - ``"subject hit on 'docker delete'"`` (best case)
      - ``"tool_id hit on 'docker'; statement hit on 'remove'"``
      - ``"statement hit on 'container'"`` (weakest, often fallback territory)
    """
    if not question_tokens:
        return 0.0, ""

    # Dedupe the question tokens before scoring. Without this, a question
    # like "docker docker docker" gets each occurrence counted as a
    # separate hit and inflates the score by ~70% relative to the same
    # question with each keyword written once. The audit caught this;
    # the regression is at tests/test_query_ask.py::
    # test_repeated_keywords_do_not_inflate_score.
    unique_question_tokens = set(question_tokens)

    subject_tokens = set(_tokenize_question(claim.subject))
    statement_tokens = set(_tokenize_question(claim.statement))
    tool_tokens = set(_tokenize_question(claim.tool_id))

    subject_hits = sorted(unique_question_tokens & subject_tokens)
    tool_hits = sorted(unique_question_tokens & tool_tokens)
    statement_hits = sorted(unique_question_tokens & statement_tokens)

    raw_score = (
        3.0 * len(subject_hits)
        + 2.0 * len(tool_hits)
        + 1.0 * len(statement_hits)
    )
    # Normalise by the de-duplicated question size so 2/2 unique-token
    # hits scores higher than 2/8. The max possible per question token
    # is 6.0 (subject + tool + statement all hit); subject + statement
    # is the common upper bound.
    score = raw_score / (len(unique_question_tokens) * 3.0)

    if score == 0.0:
        return 0.0, ""

    # Build the human-readable reason. Subject hits dominate the
    # narrative ("we matched on the command name itself"); tool and
    # statement hits are mentioned only when they're the primary signal.
    # Lists are already sorted + unique from the intersection above.
    parts: list[str] = []
    if subject_hits:
        parts.append(f"subject hit on {subject_hits!r}")
    if tool_hits:
        parts.append(f"tool_id hit on {tool_hits!r}")
    if statement_hits and not subject_hits:
        # Only surface statement-only matches when there's nothing
        # stronger — otherwise the reason gets noisy.
        parts.append(f"statement hit on {statement_hits!r}")

    reason = "; ".join(parts) if parts else "lexical token overlap"
    return score, reason


def _claim_to_answer(
    claim: KnowledgeClaim,
    *,
    match_reason: str,
    latest_result: VerificationResult | None,
    match_score: float | None = None,
) -> Answer:
    """Project a `KnowledgeClaim` into an `Answer` for the
    AskResponse. Mirrors `_summarise_tool_spec`'s role for search_tools.

    `latest_result` is the most recent `VerificationResult` for this
    claim (the caller batch-fetches via `get_latest_verification_results`
    to keep ask() at one extra query regardless of fan-out). The
    orchestrator writes confidence into the claim record itself, but
    verification_level lives only on the result row — so we read it
    from there.

    Stage 22-stretch — ``match_score`` is the (now-hybrid) score from the
    query engine's lexical + semantic ranking pipeline. When provided,
    we report it as ``Answer.confidence`` because it answers the
    question "how confident are we this claim is RELEVANT to the
    question." Verification level still carries the trust signal
    separately, so the SDK's ``is_useful`` (confidence ≥ 0.6 AND level
    != L0_unverified) becomes a clean composition: match-quality AND
    trust.

    Callers that don't run through the ranking pipeline (validate_command,
    direct claim retrieval) pass ``match_score=None`` and we fall back
    to the orchestrator's claim confidence — preserving v0.2 behavior
    for non-ask code paths.
    """
    if latest_result is not None:
        verification_level = latest_result.verification_level
        claim_confidence = latest_result.confidence
    else:
        verification_level = VerificationLevel.L1_SCHEMA_VALID
        claim_confidence = claim.confidence or 0.0

    if match_score is None:
        confidence = claim_confidence
    else:
        # Clamp into [0, 1] in case the hybrid math produces a tiny
        # overshoot from float precision.
        confidence = max(0.0, min(1.0, match_score))

    return Answer(
        claim_id=claim.claim_id,
        subject=claim.subject,
        statement=claim.statement,
        tool_id=claim.tool_id,
        confidence=confidence,
        confidence_band=band_for_score(confidence),
        verification_status=claim.verification_status,
        verification_level=verification_level,
        risk_level=claim.risk_level,
        evidence=[_project_evidence(e) for e in claim.evidence],
        match_reason=match_reason,
    )


def _surface_for_tool_id(tool_id: str) -> str:
    if "-" not in tool_id:
        return ""
    return tool_id.split("-", 1)[1]


def _question_prefers_informational_surface(question_tokens: list[str]) -> bool:
    token_set = set(question_tokens)
    return bool(token_set & (_TROUBLESHOOTING_TOKENS | _WORKFLOW_TOKENS))


def _apply_ask_bias(
    claim: KnowledgeClaim,
    *,
    base_score: float,
    question_tokens: list[str],
) -> tuple[float, str]:
    adjusted = base_score
    reasons: list[str] = []
    surface = _surface_for_tool_id(claim.tool_id)
    token_set = set(question_tokens)

    if claim.verification_status != VerificationStatus.ACCEPTED:
        adjusted -= 0.08
        reasons.append(f"status={claim.verification_status.value}")

    if surface == "errors":
        if token_set & _TROUBLESHOOTING_TOKENS:
            adjusted += 0.05
            reasons.append("errors surface fits troubleshooting question")
        else:
            adjusted -= 0.18
            reasons.append("errors surface demoted for general lookup")
    elif surface == "recipes":
        if token_set & (_WORKFLOW_TOKENS | _TROUBLESHOOTING_TOKENS):
            adjusted += 0.02
            reasons.append("recipes surface fits workflow question")
        else:
            adjusted -= 0.10
            reasons.append("recipes surface demoted for general lookup")
    elif surface and surface not in _INFORMATIONAL_SURFACES:
        adjusted += 0.03
        reasons.append(f"{surface} surface preferred for factual lookup")

    adjusted = max(0.0, adjusted)
    return adjusted, "; ".join(reasons)


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


def _workflow_match_reason(spec: WorkflowSpec, query: str) -> str:
    if not query:
        return "no-query (all workflows)"
    goal = (spec.goal or "").lower()
    if goal == query:
        return "goal exact"
    if query in goal:
        return f"goal substring '{query}'"
    if goal:
        goal_tokens = set(goal.split())
        query_tokens = set(query.split())
        overlap = goal_tokens & query_tokens
        if overlap:
            return f"goal token overlap {sorted(overlap)!r}"
    return "workflow goal match"


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
