"""End-to-end tests for the SDK against a live uvicorn-hosted backend.

Tests drive both clients (:class:`Ayiru`, :class:`AsyncAyiru`) against a
real HTTP server bound to a free localhost port — the closest possible
mirror of production use. If the SDK silently mis-parses a server
response, that surfaces here as a Pydantic validation error or a wrong
field value."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest

from ayiru_client import (
    Answer,
    AskResponse,
    AsyncAyiru,
    Ayiru,
    AyiruError,
    SearchToolsResponse,
    ValidateCommandResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_minimal_claim(store, *, subject: str = "docker volume rm") -> str:
    """Insert one verified claim with a unique id so per-test inserts
    don't collide. Returns the claim_id for the caller to assert on."""

    from app.schemas.claim import KnowledgeClaim  # noqa: PLC0415
    from app.schemas.enums import (  # noqa: PLC0415
        ClaimType,
        EvidenceType,
        RiskLevel,
        TrustLevel,
    )
    from app.schemas.evidence import Evidence  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:8]
    body = f"{subject} <volume> removes a local volume. [{suffix}]"
    evidence = Evidence(
        evidence_id=f"ev-test-{suffix}",
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri="https://docs.docker.com/reference/cli/docker/volume/rm/",
        excerpt=body,
        hash=f"sha256:{sha256(body.encode()).hexdigest()}",
        captured_at=now,
        trust_level=TrustLevel.HIGH,
    )
    claim = KnowledgeClaim(
        claim_id=f"claim-test-{suffix}",
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject=subject,
        statement=body,
        tool_id="docker",
        submitted_by="sdk-tests",
        evidence=[evidence],
        risk_level=RiskLevel.MEDIUM,
        created_at=now,
    )
    store.create(claim)
    return claim.claim_id


# ---------------------------------------------------------------------------
# Sync client — over a real HTTP socket
# ---------------------------------------------------------------------------


def test_sync_ask_returns_typed_response(base_url, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    with Ayiru(base_url=base_url) as client:
        resp = client.ask("how do I remove a docker volume")
    assert isinstance(resp, AskResponse)
    assert resp.question == "how do I remove a docker volume"
    assert len(resp.answers) >= 1


def test_sync_ask_is_useful_property(base_url, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    with Ayiru(base_url=base_url) as client:
        resp = client.ask("how do I remove a docker volume")
    top = resp.top
    assert top is not None
    expected = (
        top.confidence >= 0.6 and top.verification_level != "L0_unverified"
    )
    assert top.is_useful is expected


def test_sync_ask_on_unknown_topic_recommends_fallback(base_url) -> None:
    with Ayiru(base_url=base_url) as client:
        resp = client.ask("what is the airspeed velocity of an unladen swallow")
    assert resp.fallback_recommended is True
    assert resp.is_useful is False
    assert resp.estimated_tokens_saved == 0


def test_sync_search_tools_returns_typed_response(base_url) -> None:
    with Ayiru(base_url=base_url) as client:
        resp = client.search_tools()
    assert isinstance(resp, SearchToolsResponse)
    assert resp.limit >= 1
    assert resp.offset == 0


def test_sync_validate_command_returns_typed_response(
    base_url, claim_store
) -> None:
    _seed_minimal_claim(claim_store)
    with Ayiru(base_url=base_url) as client:
        resp = client.validate_command(
            tool_id="docker", command="docker volume rm my-volume"
        )
    assert isinstance(resp, ValidateCommandResponse)
    assert resp.tool_id == "docker"


def test_sync_get_tool_spec_raises_on_unknown(base_url) -> None:
    with Ayiru(base_url=base_url) as client, pytest.raises(AyiruError) as exc:
        client.get_tool_spec("definitely-not-a-real-tool")
    assert exc.value.status_code == 404
    assert exc.value.code == "CANONICAL_SPEC_NOT_FOUND"


def test_sync_savings_returns_typed_response(base_url) -> None:
    with Ayiru(base_url=base_url) as client:
        resp = client.savings("7d")
    assert resp.window == "7d"
    assert resp.total_queries_served >= 0


def test_sync_api_key_attaches_bearer_header(base_url) -> None:
    client = Ayiru(base_url=base_url, api_key="test-key")
    try:
        assert client._http.headers["Authorization"] == "Bearer test-key"
    finally:
        client.close()


def test_sync_no_api_key_omits_bearer_header(base_url) -> None:
    client = Ayiru(base_url=base_url)
    try:
        assert "Authorization" not in client._http.headers
    finally:
        client.close()


def test_sync_user_agent_identifies_sdk(base_url) -> None:
    client = Ayiru(base_url=base_url)
    try:
        assert client._http.headers["User-Agent"].startswith("ayiru-client-py/")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Async client — same server, same DB
# ---------------------------------------------------------------------------


async def test_async_ask_returns_typed_response(base_url, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    async with AsyncAyiru(base_url=base_url) as client:
        resp = await client.ask("how do I remove a docker volume")
    assert isinstance(resp, AskResponse)
    assert len(resp.answers) >= 1


async def test_async_ask_on_unknown_topic_recommends_fallback(base_url) -> None:
    async with AsyncAyiru(base_url=base_url) as client:
        resp = await client.ask("how do I cook a soufflé")
    assert resp.fallback_recommended is True
    assert resp.is_useful is False


async def test_async_search_tools(base_url) -> None:
    async with AsyncAyiru(base_url=base_url) as client:
        resp = await client.search_tools()
    assert isinstance(resp, SearchToolsResponse)


async def test_async_get_tool_spec_raises_on_unknown(base_url) -> None:
    async with AsyncAyiru(base_url=base_url) as client:
        with pytest.raises(AyiruError) as exc:
            await client.get_tool_spec("nope")
    assert exc.value.status_code == 404


async def test_async_savings(base_url) -> None:
    async with AsyncAyiru(base_url=base_url) as client:
        resp = await client.savings("all")
    assert resp.window == "all"


# ---------------------------------------------------------------------------
# Models (no network)
# ---------------------------------------------------------------------------


def test_answer_is_useful_threshold() -> None:
    base = dict(
        claim_id="c1",
        subject="x",
        statement="y",
        tool_id="docker",
        confidence_band="moderate",
        evidence=[],
        match_reason="exact",
    )
    assert Answer(
        confidence=0.6, verification_level="L2_source_verified", **base
    ).is_useful is True
    assert Answer(
        confidence=0.59, verification_level="L2_source_verified", **base
    ).is_useful is False
    assert Answer(
        confidence=0.95, verification_level="L0_unverified", **base
    ).is_useful is False


def test_answer_accepts_risk_level_none() -> None:
    """Regression for P0-1 (2026-05-26 second-pass audit): the server's
    RiskLevel.NONE value is emitted as the string "none" for no-risk
    claims (e.g. read-only queries cleared by safety_policy.py). The
    SDK must accept it as a valid wire value alongside the other four
    risk levels."""

    answer = Answer(
        claim_id="c1",
        subject="git log",
        statement="git log shows commit history.",
        tool_id="git",
        confidence=0.9,
        confidence_band="high",
        verification_level="L2_source_verified",
        risk_level="none",
        evidence=[],
        match_reason="exact",
    )
    assert answer.risk_level == "none"


def test_ask_response_top_is_none_on_empty_answers() -> None:
    resp = AskResponse(
        question="q",
        answers=[],
        fallback_recommended=True,
        estimated_tokens_saved=0,
        generated_at=datetime.now(timezone.utc),
    )
    assert resp.top is None
    assert resp.is_useful is False


def test_ayiru_error_format() -> None:
    err = AyiruError(
        status_code=404,
        code="CANONICAL_SPEC_NOT_FOUND",
        message="no spec for foo",
        details={"tool_id": "foo"},
    )
    s = str(err)
    assert "404" in s
    assert "CANONICAL_SPEC_NOT_FOUND" in s
    assert "no spec for foo" in s
    assert err.details == {"tool_id": "foo"}
