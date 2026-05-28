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
from app.schemas.claim import KnowledgeClaim
from app.schemas.query import (
    Answer,
    AskResponse,
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

    # -------- ask --------

    def ask(
        self,
        *,
        question: str,
        limit: int = 5,
        tool_id_hint: str | None = None,
    ) -> AskResponse:
        """Stage 17 — the headline v0.2 retrieval surface.

        Lexical token-overlap ranking against accepted claims. Mirrors
        the patterns in `search_tools` and `_score_tool_spec`: pull
        candidates from the store, score in memory, return the top
        `limit` with cited evidence.

        Filters to `verification_status='accepted'` only — the matcher
        deliberately excludes PENDING and REQUIRES_HUMAN_REVIEW claims
        so the response is never informational on a hit. Stage 19's
        curated/uncurated split will revisit this with an opt-in flag.

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
                fallback_recommended=True,
                tool_id_hint=tool_id_hint,
                fallback_reason="all_stopwords",
            )
            return AskResponse(
                question=normalized_question,
                answers=[],
                fallback_recommended=True,
                estimated_tokens_saved=0,
                generated_at=generated_at,
            )

        if limit < 1:
            limit = 1
        if limit > 20:
            limit = 20

        # Pull every accepted claim (optionally narrowed by tool_id_hint).
        # The v0.2 graph holds ~5k accepted claims after Stage 20; in-memory
        # ranking is comfortably under 100ms for that scale. Embeddings +
        # SQL pre-filter land in v0.2.x stretch / v0.3.
        normalized_hint: str | None = None
        if tool_id_hint is not None:
            normalized_hint = tool_id_hint.strip().lower() or None

        # Stage 19: pull both ACCEPTED (curated, full-orchestrator-blessed)
        # claims AND PENDING claims (uncurated L0 state — Stage 20's bulk
        # ingest lands here). REJECTED / CONFLICT_DETECTED claims are
        # excluded downstream via the latest-verification lookup so the
        # matcher never surfaces "the orchestrator said no" content.
        # validate_command stays strict — it requires L2+ via command_matcher.
        candidates = _paginate_all(
            lambda lim, off: self._store.list(
                tool_id=normalized_hint,
                limit=lim,
                offset=off,
            ),
            page_size=int(_query_policy()["default_search_limit"]),
            hard_cap=10_000,
        )

        # Stage 19: uncurated tools (not in the v2 contract's curated set)
        # surface with a marker in match_reason so the caller can tell
        # graph-blessed answers apart from L0 contributions. Loaded once
        # per ask() call; the inner set lookup is O(1).
        from app.services.claim_store import _curated_tool_ids
        curated_set = _curated_tool_ids()

        # Same exclusion list the matcher uses — never surface claims
        # whose orchestrator decision explicitly distrusts them.
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
            scored.append((score, claim.claim_id, claim, reason))

        # Stage 22-stretch — semantic re-rank.
        # For each lexically-scored candidate that has a stored embedding,
        # replace its score with a hybrid of lexical + cosine-similarity.
        # Candidates without an embedding (un-reindexed legacy rows) keep
        # their lexical score so the engine still works gracefully.
        scored = self._semantic_rerank(normalized_question, scored)

        # Sort: score DESC, claim_id ASC (deterministic tie-break).
        scored.sort(key=lambda row: (-row[0], row[1]))

        top = scored[:limit]
        top_score = top[0][0] if top else 0.0
        fallback = top_score < _ASK_SCORE_THRESHOLD

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
            fallback_recommended=fallback,
            tool_id_hint=tool_id_hint,
            fallback_reason=("below_threshold" if fallback else None),
        )

        return AskResponse(
            question=normalized_question,
            answers=answers,
            fallback_recommended=fallback,
            estimated_tokens_saved=estimated_savings,
            generated_at=generated_at,
        )

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
    """Project an accepted `KnowledgeClaim` into an `Answer` for the
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
        verification_level=verification_level,
        risk_level=claim.risk_level,
        evidence=[_project_evidence(e) for e in claim.evidence],
        match_reason=match_reason,
    )


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
