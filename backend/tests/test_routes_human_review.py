"""Stage 13 HTTP-layer tests: human review queue + decision endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import (
    ConfidenceBand,
    OrchestratorDecision,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.ids import generate_verification_id
from tests.helpers import risk_assessment, valid_sha256


@pytest.fixture
def client(tmp_path):
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'review.db'}")
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client, store
    app.dependency_overrides.clear()


def _claim_payload(claim_id: str = "claim_review_route_001") -> dict:
    return {
        "claim_id": claim_id,
        "claim_type": "cli_command_exists",
        "subject": "gh repo delete",
        "statement": "Deletes a GitHub repository.",
        "tool_id": "github-cli",
        "submitted_by": "cli-agent",
        "risk_level": "critical",
        "evidence": [
            {
                "evidence_id": f"ev_{claim_id}",
                "evidence_type": "official_docs",
                "source_uri": "https://docs.github.com/x",
                "excerpt": "deletes",
                "hash": valid_sha256(f"ev_{claim_id}"),
                "captured_at": "2026-05-18T00:00:00+00:00",
                "trust_level": "high",
            }
        ],
    }


def _seed_verification(store: ClaimStore, claim_id: str, level: VerificationLevel) -> None:
    breakdown = ConfidenceBreakdown(
        score=0.82, band=ConfidenceBand.HIGH, components=[], caps_applied=[], penalties=[]
    )
    store.save_verification_result(
        VerificationResult(
            verification_id=generate_verification_id(),
            claim_id=claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
            verification_level=level,
            confidence=0.82,
            confidence_band=ConfidenceBand.HIGH,
            confidence_breakdown=breakdown,
            risk_assessment=risk_assessment(RiskLevel.CRITICAL),
            reason_codes=["seeded"],
            reasons=["seeded"],
            verified_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        )
    )


# ---------------------------------------------------------------------------
# POST /verification/human-review
# ---------------------------------------------------------------------------


def test_approved_review_at_l3_promotes_via_http(client) -> None:
    test_client, store = client
    assert test_client.post("/claims", json=_claim_payload()).status_code == 201
    _seed_verification(store, "claim_review_route_001", VerificationLevel.L3_RUNTIME_VERIFIED)

    response = test_client.post(
        "/verification/human-review",
        json={
            "claim_id": "claim_review_route_001",
            "reviewer_id": "alice",
            "decision": "approved",
            "notes": "ok",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["promoted_to_l5"] is True
    assert payload["new_verification_level"] == VerificationLevel.L5_HUMAN_AUDITED.value


def test_review_for_missing_claim_returns_404(client) -> None:
    test_client, _ = client
    response = test_client.post(
        "/verification/human-review",
        json={
            "claim_id": "claim_does_not_exist",
            "reviewer_id": "alice",
            "decision": "approved",
        },
    )
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "CLAIM_NOT_FOUND"
    assert "claim_does_not_exist" in body["message"]


def test_invalid_decision_returns_422(client) -> None:
    test_client, _ = client
    response = test_client.post(
        "/verification/human-review",
        json={
            "claim_id": "claim_x",
            "reviewer_id": "alice",
            "decision": "maybe",  # not a valid enum value
        },
    )
    assert response.status_code == 422


def test_response_envelope_has_no_extra_fields(client) -> None:
    test_client, store = client
    assert test_client.post("/claims", json=_claim_payload()).status_code == 201
    _seed_verification(store, "claim_review_route_001", VerificationLevel.L2_SOURCE_VERIFIED)

    response = test_client.post(
        "/verification/human-review",
        json={
            "claim_id": "claim_review_route_001",
            "reviewer_id": "alice",
            "decision": "needs_changes",
            "notes": "more evidence please",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    expected_keys = {
        "review",
        "promoted_to_l5",
        "new_verification_level",
        "new_verification_status",
        "verification_id",
        "reasons",
    }
    assert set(payload.keys()) == expected_keys


# ---------------------------------------------------------------------------
# GET /verification/review-queue
# ---------------------------------------------------------------------------


def test_review_queue_lists_pending_claims(client) -> None:
    test_client, store = client
    # Submit + flag both as REQUIRES_HUMAN_REVIEW so the queue surfaces them.
    for claim_id in ("claim_pending_a", "claim_pending_b"):
        assert test_client.post(
            "/claims", json=_claim_payload(claim_id=claim_id)
        ).status_code == 201
        _seed_verification(store, claim_id, VerificationLevel.L2_SOURCE_VERIFIED)

    response = test_client.get("/verification/review-queue")
    assert response.status_code == 200
    payload = response.json()
    ids = {item["claim_id"] for item in payload["items"]}
    assert {"claim_pending_a", "claim_pending_b"}.issubset(ids)
    assert payload["total"] >= 2


def test_review_queue_filter_by_tool_id(client) -> None:
    test_client, store = client
    test_client.post("/claims", json=_claim_payload(claim_id="claim_tool_filter"))
    _seed_verification(
        store, "claim_tool_filter", VerificationLevel.L2_SOURCE_VERIFIED
    )

    response = test_client.get(
        "/verification/review-queue", params={"tool_id": "git"}
    )
    assert response.status_code == 200
    payload = response.json()
    # `git` is in the seed contract but no claim was created for it
    assert payload["total"] == 0
    assert payload["items"] == []


def test_review_queue_supports_pagination(client) -> None:
    test_client, store = client
    for index in range(5):
        claim_id = f"claim_pq_{index}"
        test_client.post("/claims", json=_claim_payload(claim_id=claim_id))
        _seed_verification(store, claim_id, VerificationLevel.L2_SOURCE_VERIFIED)

    response = test_client.get(
        "/verification/review-queue", params={"limit": 2, "offset": 0}
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["limit"] == 2
    assert payload["offset"] == 0
    assert payload["total"] >= 5
    assert len(payload["items"]) == 2
