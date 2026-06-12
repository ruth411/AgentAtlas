"""Client-side response models.

These mirror the subset of the Ayiru server's Pydantic schemas that the
SDK exposes. Kept independent of the backend package so callers can pip
install ayiru-client without dragging in the server code.

Unknown fields on incoming JSON are ignored (``extra="ignore"``) so the
client is forward-compatible with server-side additions; the SDK can be
upgraded on its own schedule."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Use string literals rather than re-importing enums from the server.
# The wire values mirror the server's `VerificationLevel` StrEnum exactly
# (uppercase-prefix + lowercase-suffix, e.g. ``L1_schema_valid``).
_VerificationLevel = Literal[
    "L0_unverified",
    "L1_schema_valid",
    "L2_source_verified",
    "L3_runtime_verified",
    "L4_cross_agent_verified",
    "L5_human_audited",
]

_ConfidenceBand = Literal["none", "low", "moderate", "high", "strong"]
# Server's RiskLevel StrEnum has 5 values — "none" is emitted for no-risk
# claims by safety_policy.py and consumed by risk_engine.py. Missing this
# literal here was P0-1 from the 2026-05-26 second-pass audit: real server
# responses with risk_level="none" failed Pydantic validation.
_RiskLevel = Literal["none", "low", "medium", "high", "critical"]
_MatchMethod = Literal["exact", "prefix", "none"]
_TrustLevel = Literal["low", "medium", "high"]


class _SDKModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class EvidenceCitation(_SDKModel):
    """Server projection of an :class:`Evidence` row.

    Intentionally narrow: the server omits the full excerpt and hash
    from query responses to keep verdicts compact. Callers wanting the
    full record can follow ``source_uri`` or call the dedicated
    ``/v1/claims/{id}/evidence`` endpoint.

    ``fetched_at`` is when the underlying source was captured. Agents can
    compare it to today's date to decide whether the answer may be stale.
    Older server versions omit the field; it is therefore optional."""

    evidence_type: str
    source_uri: str
    trust_level: _TrustLevel
    fetched_at: datetime | None = None


class Answer(_SDKModel):
    claim_id: str
    subject: str
    statement: str
    tool_id: str

    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: _ConfidenceBand
    verification_level: _VerificationLevel
    risk_level: _RiskLevel | None = None

    evidence: list[EvidenceCitation] = Field(default_factory=list)
    match_reason: str

    @property
    def is_useful(self) -> bool:
        """Stage 21.2 — quick heuristic for agent code: "should I return
        this to the user, or fall through to web_search?"

        True when confidence ≥ 0.6 AND verification_level is anything
        better than L0_unverified. The threshold is deliberately loose;
        callers needing a tighter bar can inspect ``confidence`` and
        ``verification_level`` directly."""

        return self.confidence >= 0.6 and self.verification_level != "L0_unverified"


class AskResponse(_SDKModel):
    question: str
    answers: list[Answer] = Field(default_factory=list)
    fallback_recommended: bool
    estimated_tokens_saved: int = Field(ge=0)
    generated_at: datetime

    @property
    def top(self) -> Answer | None:
        """Convenience: the first (highest-ranked) answer, or None on miss."""

        return self.answers[0] if self.answers else None

    @property
    def is_useful(self) -> bool:
        """True if the top answer is useful AND fallback_recommended is False."""

        if self.fallback_recommended or self.top is None:
            return False
        return self.top.is_useful


class ValidateCommandResponse(_SDKModel):
    tool_id: str
    command: str
    matched_claim_id: str | None = None
    match_method: _MatchMethod
    safe_to_auto_execute: bool
    requires_human_confirmation: bool
    risk_level: _RiskLevel | None = None
    verification_level: _VerificationLevel
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: _ConfidenceBand
    reasons: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    verdict_generated_at: datetime


class ToolMatchSummary(_SDKModel):
    tool_id: str
    name: str = ""
    interfaces: list[str] = Field(default_factory=list)
    capability_count: int = Field(ge=0)
    verified_command_count: int = Field(ge=0)
    highest_risk_level: _RiskLevel | None = None
    verification_level: _VerificationLevel
    match_reason: str


class SearchToolsResponse(_SDKModel):
    query: str = ""
    matches: list[ToolMatchSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class SavingsResponse(_SDKModel):
    total_queries_served: int = Field(ge=0)
    total_tokens_saved: int = Field(ge=0)
    estimated_usd_saved: float = Field(ge=0.0)
    fallback_count: int = Field(ge=0)
    by_top_claim: dict[str, int] = Field(default_factory=dict)
    window: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    usd_per_million_input_tokens: float = Field(gt=0.0)


# Tool spec is large and lane-dependent; keep it as a passthrough dict
# rather than mirroring 200+ lines of schema. Callers who want typed
# access can build their own Pydantic model over the dict.
ToolSpec = dict[str, Any]


__all__ = [
    "Answer",
    "AskResponse",
    "EvidenceCitation",
    "SavingsResponse",
    "SearchToolsResponse",
    "ToolMatchSummary",
    "ToolSpec",
    "ValidateCommandResponse",
]
