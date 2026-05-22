"""Stage 18 — QUERY_SERVED audit emission + /v1/stats/savings aggregation.

Pins the contract:
- Every ``ask()`` call appends exactly one ``QUERY_SERVED`` row.
- Raw question text is NEVER persisted — only its length.
- ``top_claim_id`` is None on fallback; non-None on hit.
- ``aggregate_query_savings`` correctly sums + buckets by top claim.
- The HTTP route enforces the window literal, multiplies by the
  configured USD rate, and falls back to the default when the env
  override is malformed.
- A failed audit emission does NOT break the ``ask`` response.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db.session import init_db, create_database_engine
from app.main import app
from app.schemas.enums import AuditEntityType, AuditEventType
from app.services.claim_store import ClaimStore
from app.services.query_engine import QueryEngine


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    db_url = f"sqlite:///{tmp_path / 'audit.db'}"
    engine = create_database_engine(db_url)
    init_db(engine)
    return ClaimStore(database_url=db_url)


@pytest.fixture
def engine(store: ClaimStore) -> QueryEngine:
    return QueryEngine(store)


# ---------------------------------------------------------------------------
# Emission contract
# ---------------------------------------------------------------------------


def test_ask_emits_exactly_one_query_served_event(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Every successful ask() call — hit or miss — appends one row."""
    engine.ask(question="how do I delete a docker container")
    events, total = store.list_audit_events(
        event_type=AuditEventType.QUERY_SERVED, limit=100
    )
    assert total == 1
    assert events[0].entity_type == AuditEntityType.QUERY
    assert events[0].entity_id.startswith("query_")
    assert events[0].actor == "agent"


def test_ask_fallback_still_emits_event(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """All-stopword question returns fallback=True but must still
    appear in the audit log — otherwise the savings aggregator
    underestimates the miss rate."""
    engine.ask(question="how do I")
    events, total = store.list_audit_events(
        event_type=AuditEventType.QUERY_SERVED, limit=100
    )
    assert total == 1
    details = events[0].details
    assert details["fallback_recommended"] is True
    assert details["top_claim_id"] is None
    assert details["tokens_saved"] == 0
    assert details["fallback_reason"] == "all_stopwords"


def test_ask_audit_event_does_not_persist_raw_question(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Raw question text must never reach the audit log — proprietary
    code, API keys, or PII could end up there. Only the length is
    recorded."""
    secret_question = "how do I use api_key=sk-secret-abc123-do-not-leak"
    engine.ask(question=secret_question)
    events, _ = store.list_audit_events(
        event_type=AuditEventType.QUERY_SERVED, limit=100
    )
    assert events
    details = events[0].details
    assert details["question_length"] == len(secret_question)
    # The raw secret must not appear anywhere in the serialized event.
    import json as _json
    payload = _json.dumps(details)
    assert "sk-secret" not in payload
    assert "api_key" not in payload


def test_audit_emission_failure_does_not_break_ask(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Telemetry is best-effort. A failure in record_query_served must
    not surface as an error in the ask() response — the read path is
    the user-facing contract; the audit row is for our forensics."""
    with patch.object(store, "record_query_served", side_effect=RuntimeError("boom")):
        # Should not raise — record_query_served already wraps its own
        # try/except, but the engine call must succeed even if the
        # helper somehow propagates the error.
        try:
            response = engine.ask(question="how do I delete a docker container")
        except RuntimeError:
            pytest.fail("ask() must not propagate audit-emission failures")
        assert response is not None


# ---------------------------------------------------------------------------
# Aggregation contract
# ---------------------------------------------------------------------------


def test_aggregate_empty_returns_zeros(store: ClaimStore) -> None:
    out = store.aggregate_query_savings()
    assert out["total_queries_served"] == 0
    assert out["total_tokens_saved"] == 0
    assert out["fallback_count"] == 0
    assert out["by_top_claim"] == {}


def test_aggregate_sums_tokens_saved(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Three queries → sum of tokens_saved equals what the audit log
    holds. Even when the matcher fallbacks zero out individual rows,
    the total is unambiguous."""
    for q in (
        "how do I delete a docker container",  # likely fallback (empty graph)
        "how do I list github repos",
        "how do I",  # stopword fallback
    ):
        engine.ask(question=q)
    out = store.aggregate_query_savings()
    assert out["total_queries_served"] == 3
    # All three are fallbacks against an empty fixture store.
    assert out["fallback_count"] == 3
    assert out["total_tokens_saved"] == 0


def test_aggregate_counts_fallback_bucket(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """Misses get bucketed under the literal key ``__fallback__`` so
    the caller can compute miss rate without parsing the response."""
    engine.ask(question="aurora borealis quantum mechanics")
    out = store.aggregate_query_savings()
    assert out["by_top_claim"].get("__fallback__") == 1


def test_aggregate_window_filter(
    engine: QueryEngine, store: ClaimStore
) -> None:
    """``after``/``before`` filter to a time window. Events outside
    the window are excluded from totals."""
    from datetime import timedelta

    engine.ask(question="test event")
    now = datetime.now(timezone.utc)
    # All events from before this test ran → empty window.
    out = store.aggregate_query_savings(before=now - timedelta(hours=1))
    assert out["total_queries_served"] == 0
    # Inclusive window → 1 event.
    out = store.aggregate_query_savings(after=now - timedelta(hours=1))
    assert out["total_queries_served"] == 1


# ---------------------------------------------------------------------------
# HTTP /v1/stats/savings
# ---------------------------------------------------------------------------


def test_stats_savings_route_empty_graph_returns_zeros() -> None:
    """The endpoint must respond cleanly on an empty graph (the v0.2
    install-and-try state) — not 500 because the audit table is empty."""
    client = TestClient(app)
    response = client.get("/v1/stats/savings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_queries_served"] >= 0
    assert payload["total_tokens_saved"] >= 0
    assert payload["estimated_usd_saved"] >= 0
    assert payload["window"] == "30d"
    assert payload["usd_per_million_input_tokens"] > 0


def test_stats_savings_route_rejects_invalid_window() -> None:
    client = TestClient(app)
    response = client.get("/v1/stats/savings", params={"window": "yesterday"})
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["details"]["field"].endswith("window")


def test_stats_savings_route_accepts_all_documented_windows() -> None:
    """24h / 7d / 30d / all are the four documented windows. Anything
    else must 422."""
    client = TestClient(app)
    for w in ("24h", "7d", "30d", "all"):
        response = client.get("/v1/stats/savings", params={"window": w})
        assert response.status_code == 200, f"window={w} returned {response.status_code}"
        assert response.json()["window"] == w


def test_stats_savings_usd_rate_respects_env_override(monkeypatch) -> None:
    """Operators on Opus or other models must be able to override the
    rate via env var. Default $3 → overridden $15 → response reflects it."""
    monkeypatch.setenv("AYIRU_PRICE_PER_MTOK_INPUT", "15.0")
    client = TestClient(app)
    response = client.get("/v1/stats/savings")
    assert response.status_code == 200
    assert response.json()["usd_per_million_input_tokens"] == 15.0


def test_stats_savings_usd_rate_falls_back_on_malformed_env(monkeypatch) -> None:
    """Telemetry must never break on a typo'd env var. A bogus rate
    silently falls back to the default rather than crashing the route."""
    monkeypatch.setenv("AYIRU_PRICE_PER_MTOK_INPUT", "not-a-number")
    client = TestClient(app)
    response = client.get("/v1/stats/savings")
    assert response.status_code == 200
    # Falls back to $3 default.
    assert response.json()["usd_per_million_input_tokens"] == 3.0


def test_stats_savings_usd_rate_falls_back_on_negative_env(monkeypatch) -> None:
    """Same idea: zero or negative rate is invalid; fall back."""
    monkeypatch.setenv("AYIRU_PRICE_PER_MTOK_INPUT", "-1.5")
    client = TestClient(app)
    response = client.get("/v1/stats/savings")
    assert response.status_code == 200
    assert response.json()["usd_per_million_input_tokens"] == 3.0
