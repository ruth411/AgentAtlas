"""Stage 9: Agent Query Surface — request / response schemas.

Every model uses `extra="forbid"` so unknown fields are rejected at the
boundary; every nullable optional is explicit (no implicit defaults that
could hide a missing match). Response shapes match the README's documented
`validate_command` contract verbatim — the README is the spec.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import (
    ConfidenceBand,
    EvidenceType,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
)


_TOOL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _require_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _validate_tool_id(value: str) -> str:
    if not _TOOL_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "tool_id must match [A-Za-z0-9_.-]{1,128}; "
            f"got {value!r}"
        )
    return value


# -------- validate_command --------


class ValidateCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1, max_length=512)

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, value: str) -> str:
        return _validate_tool_id(value)


class EvidenceCitation(BaseModel):
    """A projection of `Evidence` for query responses.

    Excludes the full excerpt + hash (those live in the raw artifact) so the
    verdict stays compact. Callers wanting the full evidence can follow the
    `source_uri` or pull the original via the `/claims/{id}/evidence` API."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_type: EvidenceType
    source_uri: str = Field(min_length=1, max_length=2048)
    trust_level: TrustLevel


class ValidateCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1, max_length=512)

    matched_claim_id: str | None = Field(default=None, max_length=128)
    match_method: Literal["exact", "prefix", "none"]

    safe_to_auto_execute: bool
    requires_human_confirmation: bool
    risk_level: RiskLevel | None
    verification_level: VerificationLevel
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBand

    reasons: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCitation] = Field(default_factory=list)

    verdict_generated_at: datetime

    @field_validator("verdict_generated_at")
    @classmethod
    def validate_verdict_generated_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


# -------- search_tools --------


class ToolMatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=256)
    interfaces: list[str] = Field(default_factory=list)
    capability_count: int = Field(ge=0)
    verified_command_count: int = Field(ge=0)
    highest_risk_level: RiskLevel | None
    verification_level: VerificationLevel
    match_reason: str = Field(min_length=1, max_length=256)


class SearchToolsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(default="", max_length=256)
    matches: list[ToolMatchSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


# -------- explain_risk --------


class ExplainRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=512)

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, value: str) -> str:
        return _validate_tool_id(value)


class RiskDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destructive_action: bool = False
    mutates_remote_state: bool = False
    reversible: bool = True
    requires_auth: bool = False
    may_cost_money: bool = False
    may_expose_secrets: bool = False


class ExplainRiskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=512)

    risk_level: RiskLevel | None
    risk_dimensions: RiskDimensions
    reasons: list[str] = Field(default_factory=list)
    citing_claim_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    verdict_generated_at: datetime

    @field_validator("verdict_generated_at")
    @classmethod
    def validate_verdict_generated_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


# -------- safe_workflow --------


class SafeWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    goal: str = Field(min_length=1, max_length=512)
    environment: str | None = Field(default=None, max_length=128)


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workflow_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(default="", max_length=512)
    tool_ids: list[str] = Field(default_factory=list)
    step_count: int = Field(ge=0)
    aggregate_risk_level: RiskLevel
    verification_level: VerificationLevel
    requires_confirmation: bool


class SafeWorkflowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    goal: str = Field(default="", max_length=512)
    environment: str | None = Field(default=None, max_length=128)
    matches: list[WorkflowSummary] = Field(default_factory=list)
    total: int = Field(ge=0)


# -------- ask --------
#
# Stage 17.1 — the headline v0.2 surface. An agent submits a natural-language
# question; the engine searches the verified knowledge graph and returns
# ranked, cited answers. Replaces a web_search round-trip for common dev /
# CLI / API questions.
#
# Contract notes:
# - Question is free-form prose (1–512 chars). No required structure.
# - tool_id_hint is optional — when present, narrows the search to one tool's
#   claims (cheap and useful when the agent already knows the tool).
# - limit clamps to 1..20 so a buggy caller can't drag the matcher into a
#   pathological scan; default 5 matches the docs hero example.
# - The response is verdict-like: callers get the SAME structured shape on
#   hit OR miss (empty answers + fallback_recommended=True on miss). Never
#   returns a 404 for a no-match — that's a valid result, not an error.
# - estimated_tokens_saved is a heuristic (see app.services.query_engine
#   _AVERAGE_WEB_SEARCH_TOKENS). Stage 18 wires this to the audit log.


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=5, ge=1, le=20)
    tool_id_hint: str | None = Field(default=None, max_length=128)

    @field_validator("tool_id_hint")
    @classmethod
    def validate_tool_id_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_tool_id(value)


class Answer(BaseModel):
    """One ranked answer projected from a `KnowledgeClaim`.

    Carries enough for the agent to decide whether to use the answer (or
    fall back to web search) without a follow-up round-trip: the matched
    statement, the tool it belongs to, the verification level and
    confidence the orchestrator assigned, and the cited evidence. The
    match_reason field documents *why* this claim surfaced for the
    question — useful for debugging mis-matches without re-querying."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=512)
    statement: str = Field(min_length=1)
    tool_id: str = Field(min_length=1, max_length=128)

    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBand
    verification_level: VerificationLevel
    risk_level: RiskLevel | None = None

    evidence: list[EvidenceCitation] = Field(default_factory=list)
    match_reason: str = Field(min_length=1, max_length=256)


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=512)
    answers: list[Answer] = Field(default_factory=list)

    # When True, the matcher couldn't find a confident match — the caller
    # should fall through to web_search (the v0.2 product thesis: ask first,
    # web only on miss). When False, the answers list is the authoritative
    # response and the agent should NOT escalate to web search.
    fallback_recommended: bool

    # Heuristic: how many LLM tokens the caller saved by using ask instead
    # of web_search. Zero on a fallback. Stage 18 calibrates against real
    # audit data. The field is informational — never used for billing or
    # auth decisions.
    estimated_tokens_saved: int = Field(ge=0)

    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


# -------- shared helpers exposed for the engine --------


__all__ = [
    "Answer",
    "AskRequest",
    "AskResponse",
    "EvidenceCitation",
    "ExplainRiskRequest",
    "ExplainRiskResponse",
    "RiskDimensions",
    "SafeWorkflowRequest",
    "SafeWorkflowResponse",
    "SearchToolsResponse",
    "ToolMatchSummary",
    "ValidateCommandRequest",
    "ValidateCommandResponse",
    "WorkflowSummary",
]
