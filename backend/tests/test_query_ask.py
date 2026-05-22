"""Stage 17 — QueryEngine.ask() adversarial tests.

Pin the v0.2 retrieval contract:

- ACCEPTED-only filter (PENDING and REQUIRES_HUMAN_REVIEW excluded)
- token-overlap ranking with subject > tool_id > statement weighting
- all-stopword question → fallback_recommended=True with zero
  estimated_tokens_saved
- no-match question → same fallback shape
- empty graph → same fallback shape
- limit clamps at 1..20
- tool_id_hint narrows the candidate pool
- Answer.match_reason explains why the claim surfaced
- estimated_tokens_saved tracks _AVERAGE_WEB_SEARCH_TOKENS minus the
  serialised answer size
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown, ConfidenceComponent
from app.schemas.enums import (
    ClaimType,
    ConfidenceBand,
    EvidenceType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.schemas.query import Answer, AskResponse
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore
from app.services.ids import (
    generate_claim_id,
    generate_evidence_id,
    generate_verification_id,
)
from app.services.query_engine import QueryEngine
from tests.helpers import risk_assessment, valid_sha256


FIXED_TIME = datetime(2026, 5, 21, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


@pytest.fixture
def engine(store: ClaimStore) -> QueryEngine:
    return QueryEngine(store, now=FIXED_TIME)


def _evidence(uri: str = "https://docs.example.com/test") -> Evidence:
    return Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri=uri,
        excerpt="trusted source excerpt",
        hash=valid_sha256(uri),
        captured_at=FIXED_TIME,
        trust_level=TrustLevel.HIGH,
    )


def _band_for(score: float) -> ConfidenceBand:
    if score < 0.30:
        return ConfidenceBand.NONE
    if score < 0.55:
        return ConfidenceBand.LOW
    if score < 0.75:
        return ConfidenceBand.MODERATE
    if score < 0.90:
        return ConfidenceBand.HIGH
    return ConfidenceBand.STRONG


def _add_claim(
    store: ClaimStore,
    *,
    subject: str,
    statement: str,
    tool_id: str = "docker",
    risk: RiskLevel = RiskLevel.LOW,
    status: VerificationStatus = VerificationStatus.ACCEPTED,
    level: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED,
    confidence: float = 0.85,
) -> KnowledgeClaim:
    """Persist a claim AND its verification result so it's visible to
    ask() the same way an orchestrator-accepted claim would be.

    For non-ACCEPTED statuses we still write a result so the claim has a
    verification level on file — ask() filters by status BEFORE looking
    at level, so the status field is what gates exclusion.
    """
    claim = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        submitted_by="ask-test",
        evidence=[_evidence(f"https://docs.example.com/{subject.replace(' ', '-')}")],
        risk_level=risk,
        created_at=FIXED_TIME,
    )
    store.create(claim)
    breakdown = ConfidenceBreakdown(
        score=confidence,
        band=_band_for(confidence),
        components=[
            ConfidenceComponent(source="test", delta=confidence, reason="forced for ask test")
        ],
        caps_applied=[],
        penalties=[],
    )
    # Map status to an appropriate decision so the result row is
    # contract-valid (the orchestrator's enum mapping).
    decision = {
        VerificationStatus.ACCEPTED: OrchestratorDecision.ACCEPTED,
        VerificationStatus.PENDING: OrchestratorDecision.PENDING_MORE_EVIDENCE,
        VerificationStatus.REQUIRES_HUMAN_REVIEW: OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
    }[status]
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim.claim_id,
            decision=decision,
            verification_status=status,
            verification_level=level,
            confidence=confidence,
            confidence_band=_band_for(confidence),
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment(risk),
            reason_codes=["TEST_FORCE"],
            reasons=["forced for ask test"],
            verified_at=FIXED_TIME,
        )
    )
    return claim


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ask_returns_top_match_for_a_realistic_question(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """The canonical headline: a natural-language question about a CLI
    surfaces the matching claim, with citations, no fallback."""
    target = _add_claim(
        store,
        subject="docker rm",
        statement="docker rm removes one or more containers from the local engine.",
        tool_id="docker",
        risk=RiskLevel.CRITICAL,
    )

    response = engine.ask(question="how do I delete a docker container")

    assert isinstance(response, AskResponse)
    assert response.fallback_recommended is False
    assert len(response.answers) >= 1
    assert response.answers[0].claim_id == target.claim_id
    assert response.answers[0].tool_id == "docker"
    assert response.answers[0].risk_level == RiskLevel.CRITICAL
    # The match reason is debug surface — must say SOMETHING actionable.
    assert response.answers[0].match_reason
    # Evidence is projected (no full excerpt; just type + uri + trust).
    assert response.answers[0].evidence
    assert response.answers[0].evidence[0].source_uri.startswith("https://")


def test_ask_response_uses_timezone_aware_timestamp(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """generated_at must be tz-aware — the schema rejects naive datetimes
    on construction, so the engine has to comply."""
    _add_claim(store, subject="docker rm", statement="removes containers")
    response = engine.ask(question="docker rm container")
    assert response.generated_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_ask_ranks_subject_match_above_statement_only_match(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Token overlap on the subject is worth 3× the same overlap in the
    statement prose. A claim where "delete" is in the subject should
    outrank one where "delete" only appears in the statement."""
    subject_winner = _add_claim(
        store,
        subject="docker delete",
        statement="A made-up command for the ranking test.",
        tool_id="docker",
    )
    _add_claim(
        store,
        subject="docker remove",
        statement="docker remove and delete are sometimes used interchangeably.",
        tool_id="docker",
    )

    response = engine.ask(question="how do I delete with docker")

    assert response.fallback_recommended is False
    assert response.answers[0].claim_id == subject_winner.claim_id


def test_ask_tool_id_match_outranks_statement_only_match(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """tool_id token hits carry weight 2.0 — a question that names the
    tool should prefer claims for that tool over an unrelated tool whose
    statement happens to mention the same word."""
    docker_claim = _add_claim(
        store,
        subject="docker ps",
        statement="lists running containers in the local engine.",
        tool_id="docker",
    )
    _add_claim(
        store,
        subject="vercel deploy",
        statement="deploys an app; some teams compare this to running containers with docker.",
        tool_id="vercel-cli",
    )

    response = engine.ask(question="what does docker do for running containers")

    assert response.fallback_recommended is False
    assert response.answers[0].claim_id == docker_claim.claim_id


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_ask_returns_fallback_for_all_stopword_question(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """A question made entirely of stop-words ('how do I') has no signal
    to rank on. The engine must return fallback=True rather than scoring
    against an empty filter."""
    _add_claim(store, subject="docker rm", statement="removes containers")

    response = engine.ask(question="how do I")

    assert response.fallback_recommended is True
    assert response.answers == []
    assert response.estimated_tokens_saved == 0


def test_ask_returns_fallback_when_no_claim_overlaps(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Real graph, real question, but no lexical overlap. fallback=True
    with zero tokens saved — the agent should escalate to web_search."""
    _add_claim(store, subject="docker rm", statement="removes containers")

    response = engine.ask(question="aurora borealis quantum mechanics")

    assert response.fallback_recommended is True
    assert response.answers == []
    assert response.estimated_tokens_saved == 0


def test_ask_returns_fallback_on_empty_graph(engine: QueryEngine) -> None:
    """A pristine database has zero candidates. ask() must still return
    the typed response shape (not raise, not 404)."""
    response = engine.ask(question="how do I delete a docker container")

    assert isinstance(response, AskResponse)
    assert response.fallback_recommended is True
    assert response.answers == []


# ---------------------------------------------------------------------------
# Acceptance filter
# ---------------------------------------------------------------------------


def test_ask_excludes_pending_claims(engine: QueryEngine, store: ClaimStore) -> None:
    """Stage 19 will add an opt-in for uncurated claims. Until then, ask
    must only return claims at verification_status='accepted'."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        status=VerificationStatus.PENDING,
        level=VerificationLevel.L1_SCHEMA_VALID,
    )

    response = engine.ask(question="how do I delete a docker container")

    assert response.fallback_recommended is True
    assert response.answers == []


def test_ask_excludes_requires_human_review_claims(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """High/critical-risk claims that the orchestrator routed to human
    review must not surface in ask responses — that path is explicitly
    informational-only in validate_command, and ask() makes no such
    distinction (answers are authoritative or absent)."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        risk=RiskLevel.CRITICAL,
        status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
        level=VerificationLevel.L2_SOURCE_VERIFIED,
    )

    response = engine.ask(question="how do I delete a docker container")

    assert response.fallback_recommended is True
    assert response.answers == []


# ---------------------------------------------------------------------------
# tool_id_hint
# ---------------------------------------------------------------------------


def test_ask_tool_id_hint_narrows_to_one_tool(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """When the caller already knows the tool, the hint must cut the
    candidate pool — claims for other tools shouldn't surface even if
    their tokens overlap better."""
    target = _add_claim(
        store,
        subject="docker delete",
        statement="A made-up docker subcommand for the hint test.",
        tool_id="docker",
    )
    _add_claim(
        store,
        subject="gh delete",
        statement="Stronger lexical overlap with the question, but wrong tool.",
        tool_id="github-cli",
    )

    response = engine.ask(question="delete something", tool_id_hint="docker")

    assert response.fallback_recommended is False
    assert all(a.tool_id == "docker" for a in response.answers)
    assert response.answers[0].claim_id == target.claim_id


# ---------------------------------------------------------------------------
# Telemetry-adjacent
# ---------------------------------------------------------------------------


def test_ask_estimated_tokens_saved_is_positive_on_hit(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Heuristic: tokens-saved equals _AVERAGE_WEB_SEARCH_TOKENS (830)
    minus a quarter of the serialised JSON length. For a typical answer
    that nets ~300-700 saved tokens — definitely positive."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes one or more containers from the engine.",
    )

    response = engine.ask(question="docker rm container")

    assert response.fallback_recommended is False
    assert response.estimated_tokens_saved > 0


def test_ask_answer_carries_match_reason(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """match_reason is the debug surface — must always be populated and
    must reference at least one matched token from the question."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        tool_id="docker",
    )

    response = engine.ask(question="how do I rm a docker container")

    assert response.answers
    reason = response.answers[0].match_reason
    assert reason
    # The matcher should name at least one of the question's content
    # tokens (rm, docker, or container) in the reason.
    assert any(token in reason.lower() for token in ("rm", "docker", "container"))


# ---------------------------------------------------------------------------
# Answer shape
# ---------------------------------------------------------------------------


def test_ask_answer_projects_verification_level_from_latest_result(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """verification_level lives on the VerificationResult, not the claim
    record. ask() must batch-fetch the latest result for the top-N
    claims and surface its level on the Answer payload."""
    _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        level=VerificationLevel.L3_RUNTIME_VERIFIED,
        confidence=0.92,
    )

    response = engine.ask(question="docker rm container")

    assert response.answers
    answer = response.answers[0]
    assert isinstance(answer, Answer)
    assert answer.verification_level == VerificationLevel.L3_RUNTIME_VERIFIED
    assert answer.confidence == pytest.approx(0.92)
    assert answer.confidence_band == ConfidenceBand.STRONG


def test_ask_limit_caps_returned_answers(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """The engine clamps limit to 1..20 even if the schema's Field(le=20)
    is bypassed (e.g. when the engine is called programmatically). With
    limit=1, only the highest-scoring claim returns."""
    _add_claim(store, subject="docker rm", statement="removes containers")
    _add_claim(store, subject="docker stop", statement="stops a running container without removing it")

    response = engine.ask(question="docker container", limit=1)

    assert len(response.answers) == 1


# ---------------------------------------------------------------------------
# Audit regressions (caught in the 2026-05-21 senior-dev pass)
# ---------------------------------------------------------------------------


def test_short_command_tokens_survive_tokenization() -> None:
    """Audit regression. Earlier draft applied `_MIN_TOKEN_LENGTH = 3`
    which silently dropped half the Unix CLI vocabulary: `rm`, `ls`,
    `cd`, `mv`, `cp`, `du`, `df`, `ps`, `gh`, `jq`, `yq`, `bq`, `pg`,
    `vi`. The headline pitch is "agents look up CLI commands here
    instead of web_search" — if `rm` doesn't tokenize, the pitch is
    broken. The stop-word set already drops short noise particles
    (`a`, `i`, `is`, `to`, `do`); a length filter on top of it
    over-prunes."""
    from app.services.query_engine import _tokenize_question

    # Each of these would have returned `[]` or stripped the command
    # name under the buggy length filter. They MUST keep the command.
    assert "rm" in _tokenize_question("how do I use rm")
    assert "cd" in _tokenize_question("what does cd do")
    assert "ls" in _tokenize_question("when should I use ls -la")
    assert "gh" in _tokenize_question("how do I run gh repo list")
    assert "jq" in _tokenize_question("filter json with jq")
    # And the multi-token cases continue to work.
    assert _tokenize_question("docker rm container") == ["docker", "rm", "container"]


def test_repeated_keywords_do_not_inflate_score(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Audit regression. Earlier draft counted each occurrence of a
    question token as a separate hit, so duplicating keywords inflated
    the score by ~70% against the same claim. The fix dedupes the
    question token set before scoring.

    Strict invariant: adding duplicates of tokens already present in
    the question must NOT change the score. Two questions with the
    same unique-token set after dedup are equivalent."""
    from app.services.query_engine import _score_claim_for_question, _tokenize_question

    claim = _add_claim(
        store,
        subject="docker rm",
        statement="removes one or more containers from the engine",
        tool_id="docker",
    )
    base = _tokenize_question("delete a docker container")
    duplicated = _tokenize_question(
        "delete a docker docker docker container container container"
    )
    # Same unique-token set after dedup — must score identically.
    assert set(base) == set(duplicated)
    assert _score_claim_for_question(claim, base)[0] == (
        _score_claim_for_question(claim, duplicated)[0]
    )


def test_repeated_single_token_question_scores_same_as_one(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Stronger version of the prior test: the score for
    ``"docker docker docker"`` must equal the score for ``"docker"``
    against the same claim. They contain the same unique content; the
    matcher must treat them identically."""
    from app.services.query_engine import _score_claim_for_question, _tokenize_question

    claim = _add_claim(
        store,
        subject="docker rm",
        statement="removes containers",
        tool_id="docker",
    )
    one = _tokenize_question("docker")
    many = _tokenize_question("docker docker docker docker docker")

    assert _score_claim_for_question(claim, one)[0] == (
        _score_claim_for_question(claim, many)[0]
    )
