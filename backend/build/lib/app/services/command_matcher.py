"""Stage 9: Strict command matcher.

Given a `(tool_id, command)` pair, find the best-verified `KnowledgeClaim`
whose `subject` matches the command. Matching is deliberately conservative:

- **Exact match:** `claim.subject == command` (verbatim, after whitespace
  normalisation). Wins outright if any exact match exists.
- **Prefix match:** `command` starts with `claim.subject` AND the next
  character is whitespace OR end-of-string. So `gh repo delete --yes`
  matches a claim with subject `gh repo delete`, but `gh repos list` does
  NOT match `gh repo` (no word boundary).

When multiple claims match, we resolve in this order:
1. Prefer the LONGEST matching subject (`gh repo delete` beats `gh repo`).
2. Prefer the highest `verification_level` (L3 beats L2).
3. Prefer the highest `confidence`.
4. Prefer the most recently created claim.

Only claims whose latest `VerificationResult` is `ACCEPTED` are considered.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    VERIFICATION_LEVEL_ORDER,
    VerificationLevel,
    VerificationStatus,
)
from app.services.claim_store import ClaimStore


_WHITESPACE_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class CommandMatch:
    claim: KnowledgeClaim
    match_method: str  # "exact" | "prefix"
    matched_subject: str
    confidence: float
    verification_level: VerificationLevel


def normalize_command(command: str) -> str:
    """Collapse internal whitespace runs to single spaces; strip outer."""
    return _WHITESPACE_RUN.sub(" ", command).strip()


_CANDIDATE_PAGE_SIZE = 500
_CANDIDATE_HARD_CAP = 10_000

# Statuses that explicitly mean "do not surface this claim in any verdict":
# REJECTED is the orchestrator's explicit thumbs-down; CONFLICT_DETECTED
# means two sources disagree so surfacing one would be misleading. Every
# other status (ACCEPTED, PENDING, REQUIRES_HUMAN_REVIEW) is visible to
# the matcher — the engine's confidence + level gates handle whether the
# matched claim is *trustworthy enough* to drive auto-execution. Without
# this relaxation, single-evidence ingestion claims (the typical Stage 7
# output) sit at status=PENDING and validate-command silently returns
# default-deny for everything — breaking the end-to-end pipeline.
_EXCLUDED_STATUSES: frozenset[VerificationStatus] = frozenset(
    {VerificationStatus.REJECTED, VerificationStatus.CONFLICT_DETECTED}
)

# Minimum verification level a match must have reached. Below this we
# don't have source-verified evidence and the verdict should treat the
# claim as if it doesn't exist.
_MIN_MATCH_LEVEL: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED


def match_command(
    *,
    tool_id: str,
    command: str,
    store: ClaimStore,
    candidate_hard_cap: int = _CANDIDATE_HARD_CAP,
) -> CommandMatch | None:
    """Return the best-verified claim matching the command, or None.

    Paginates through ACCEPTED claims for the tool in `_CANDIDATE_PAGE_SIZE`
    chunks until the store is exhausted or `candidate_hard_cap` is hit.
    The pagination matters: an earlier single-page implementation silently
    dropped any matching claim that fell past the 500th in creation order
    once a tool grew beyond that — a real failure mode for breadth scaling.
    The hard cap exists so a pathologically large tool can't make the query
    unbounded in memory; if a real tool ever exceeds it we should add a
    SQL-level subject filter instead of growing this in-memory scan.
    """

    normalized = normalize_command(command)
    if not normalized:
        return None

    exact_matches: list[CommandMatch] = []
    prefix_matches: list[CommandMatch] = []
    scanned = 0
    offset = 0
    while scanned < candidate_hard_cap:
        # No verification_status filter at the SQL layer: we want to see
        # PENDING and REQUIRES_HUMAN_REVIEW claims too (the engine's gates
        # will refuse to auto-execute on them, but their existence is
        # informative). We filter out REJECTED / CONFLICT_DETECTED in the
        # Python loop below since SQLAlchemy's `==` filter can't express
        # "status NOT IN (...)" cleanly.
        page = store.list(
            tool_id=tool_id,
            limit=_CANDIDATE_PAGE_SIZE,
            offset=offset,
        )
        if not page:
            break
        scanned += len(page)
        offset += len(page)
        # Batch the latest-verification lookups for this page: one SQL
        # query for the whole page instead of N. Critical for breadth
        # ingestion where a tool may have hundreds of claims.
        latest_by_claim_id = store.get_latest_verification_results(
            [claim.claim_id for claim in page]
        )
        for claim in page:
            latest = latest_by_claim_id.get(claim.claim_id)
            if latest is None:
                # Claim was created but never verified. Not a candidate.
                continue
            # Exclude claims whose orchestrator decision explicitly says
            # "do not trust this." See `_EXCLUDED_STATUSES` rationale above.
            if latest.verification_status in _EXCLUDED_STATUSES:
                continue
            # Require source-verified evidence: anything below L2 hasn't
            # passed the orchestrator's evidence policy gate.
            if (
                VERIFICATION_LEVEL_ORDER[latest.verification_level]
                < VERIFICATION_LEVEL_ORDER[_MIN_MATCH_LEVEL]
            ):
                continue
            confidence = (
                claim.confidence
                if claim.confidence is not None
                else latest.confidence
            )
            subject_normalized = normalize_command(claim.subject)
            if not subject_normalized:
                continue

            if subject_normalized == normalized:
                exact_matches.append(
                    CommandMatch(
                        claim=claim,
                        match_method="exact",
                        matched_subject=subject_normalized,
                        confidence=confidence,
                        verification_level=latest.verification_level,
                    )
                )
                continue

            # Prefix with word boundary: the next char after the subject
            # must be whitespace, so `gh rep` won't match a claim `gh repo`.
            if normalized.startswith(subject_normalized + " "):
                prefix_matches.append(
                    CommandMatch(
                        claim=claim,
                        match_method="prefix",
                        matched_subject=subject_normalized,
                        confidence=confidence,
                        verification_level=latest.verification_level,
                    )
                )

        if len(page) < _CANDIDATE_PAGE_SIZE:
            break  # last page, no need to ask for another

    # Exact wins outright. If multiple exact matches exist, resolve by the
    # standard tie-breaker (level > confidence > created_at).
    if exact_matches:
        return _best(exact_matches)

    if prefix_matches:
        return _best_prefix(prefix_matches)

    return None


def _best(candidates: list[CommandMatch]) -> CommandMatch:
    """Tie-break: highest verification_level, then highest confidence,
    then most recent created_at."""
    return max(
        candidates,
        key=lambda c: (
            VERIFICATION_LEVEL_ORDER[c.verification_level],
            c.confidence,
            c.claim.created_at,
        ),
    )


def _best_prefix(candidates: list[CommandMatch]) -> CommandMatch:
    """For prefix matches, the LONGEST matching subject wins first. This
    ensures `gh repo delete --yes` matches `gh repo delete`, not the more
    general `gh repo` claim, even if the latter is better-verified."""
    return max(
        candidates,
        key=lambda c: (
            len(c.matched_subject),
            VERIFICATION_LEVEL_ORDER[c.verification_level],
            c.confidence,
            c.claim.created_at,
        ),
    )


__all__ = [
    "CommandMatch",
    "match_command",
    "normalize_command",
]
