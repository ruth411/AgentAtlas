from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.claim_store import ClaimStore, get_claim_store
from tests.helpers import valid_sha256


@pytest.fixture
def client(tmp_path):
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas-test.db'}")

    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _claim_payload() -> dict:
    return {
        "claim_id": "claim_123",
        "claim_type": "cli_command_exists",
        "subject": "gh repo delete",
        "statement": "The GitHub CLI command `gh repo delete` deletes a repository.",
        "tool_id": "github-cli",
        "submitted_by": "cli-agent",
        "evidence": [
            {
                "evidence_id": "ev_123",
                "evidence_type": "cli_help_output",
                "source_uri": "local://gh-repo-delete-help.txt",
                "excerpt": "Delete a repository on GitHub.",
                "hash": valid_sha256("ev_123"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc).isoformat(),
                "trust_level": "high",
            }
        ],
        "risk_level": "critical",
    }


def test_post_claim_sets_pending_status_and_null_confidence(client) -> None:
    response = client.post("/claims", json=_claim_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["verification_status"] == "pending"
    assert body["confidence"] is None
    assert body["risk_level_classified"] is None
    assert body["risk_assessment"] is None


def test_post_claim_rejects_system_managed_fields(client) -> None:
    payload = _claim_payload()
    payload["verification_status"] = "accepted"
    payload["confidence"] = 0.91

    response = client.post("/claims", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CLAIM_SCHEMA"


def test_get_claims_returns_created_claim(client) -> None:
    client.post("/claims", json=_claim_payload())

    response = client.get("/claims")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_claims_supports_stage_2_query_filters(client) -> None:
    client.post("/claims", json=_claim_payload())

    response = client.get(
        "/claims",
        params={
            "tool_id": "github-cli",
            "verification_status": "pending",
            "risk_level": "critical",
            "claim_type": "cli_command_exists",
            "submitted_by": "cli-agent",
        },
    )

    assert response.status_code == 200
    assert [claim["claim_id"] for claim in response.json()] == ["claim_123"]


def test_get_claims_supports_classified_risk_filter_after_verification(client) -> None:
    payload = _claim_payload()
    payload["risk_level"] = "low"
    client.post("/claims", json=payload)
    client.post("/claims/claim_123/verify")

    critical = client.get("/claims", params={"risk_level_classified": "critical"})
    low = client.get("/claims", params={"risk_level_classified": "low"})

    assert critical.status_code == 200
    assert [claim["claim_id"] for claim in critical.json()] == ["claim_123"]
    assert low.status_code == 200
    assert low.json() == []


def test_post_claim_generates_missing_claim_and_evidence_ids(client) -> None:
    payload = _claim_payload()
    payload.pop("claim_id")
    payload["evidence"][0].pop("evidence_id")

    response = client.post("/claims", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["claim_id"].startswith("claim_")
    assert body["evidence"][0]["evidence_id"].startswith("ev_")


def test_get_claims_rejects_invalid_query_params_with_structured_error(client) -> None:
    response = client.get("/claims", params={"risk_level": "catastrophic"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUERY_PARAMETER"
    assert response.json()["error"]["details"]["field"] == "query.risk_level"


def test_get_claim_by_id_returns_claim(client) -> None:
    client.post("/claims", json=_claim_payload())

    response = client.get("/claims/claim_123")

    assert response.status_code == 200
    assert response.json()["claim_id"] == "claim_123"


def test_get_claim_by_id_returns_404_for_unknown_claim(client) -> None:
    response = client.get("/claims/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CLAIM_NOT_FOUND"


def test_get_claim_evidence_returns_attached_evidence(client) -> None:
    client.post("/claims", json=_claim_payload())

    response = client.get("/claims/claim_123/evidence")

    assert response.status_code == 200
    evidence = response.json()
    assert len(evidence) == 1
    assert evidence[0]["evidence_id"] == "ev_123"
    assert evidence[0]["evidence_type"] == "cli_help_output"
    assert evidence[0]["source_uri"] == "local://gh-repo-delete-help.txt"
    assert evidence[0]["trust_level"] == "medium"


def test_get_claim_evidence_returns_404_for_unknown_claim(client) -> None:
    response = client.get("/claims/missing/evidence")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CLAIM_NOT_FOUND"


def test_get_evidence_by_id_returns_evidence(client) -> None:
    client.post("/claims", json=_claim_payload())

    response = client.get("/evidence/ev_123")

    assert response.status_code == 200
    assert response.json()["evidence_id"] == "ev_123"


def test_get_evidence_by_id_returns_404_for_unknown_evidence(client) -> None:
    response = client.get("/evidence/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"


def test_post_claim_rejects_invalid_schema_with_structured_error(client) -> None:
    payload = _claim_payload()
    payload["statement"] = ""

    response = client.post("/claims", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CLAIM_SCHEMA"


def test_post_claim_rejects_duplicate_claim_id(client) -> None:
    payload = _claim_payload()

    first = client.post("/claims", json=payload)
    second = client.post("/claims", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CLAIM_ALREADY_EXISTS"


def test_post_claim_rejects_duplicate_evidence_id_without_storing_second_claim(client) -> None:
    first_payload = _claim_payload()
    second_payload = _claim_payload()
    second_payload["claim_id"] = "claim_456"

    first = client.post("/claims", json=first_payload)
    second = client.post("/claims", json=second_payload)
    claims = client.get("/claims")

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EVIDENCE_ALREADY_EXISTS"
    assert [claim["claim_id"] for claim in claims.json()] == ["claim_123"]


def test_verify_claim_flags_understated_risk(client) -> None:
    payload = _claim_payload()
    payload["risk_level"] = "low"
    client.post("/claims", json=payload)

    response = client.post("/claims/claim_123/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["verification_id"].startswith("ver_")
    assert body["decision"] == "requires_human_review"
    assert body["verification_status"] == "requires_human_review"
    assert body["reason_codes"] == ["UNDERSTATED_RISK"]

    stored_claim = client.get("/claims/claim_123").json()
    assert stored_claim["verification_status"] == "requires_human_review"
    assert stored_claim["confidence"] == body["confidence"]
    assert stored_claim["risk_level_classified"] == "critical"
    assert stored_claim["risk_assessment"]["matched_rule_ids"] == ["gh.repo_delete"]


def test_get_latest_claim_verification_returns_persisted_result(client) -> None:
    payload = _claim_payload()
    payload["risk_level"] = "low"
    client.post("/claims", json=payload)
    created = client.post("/claims/claim_123/verify").json()

    response = client.get("/claims/claim_123/verification")

    assert response.status_code == 200
    assert response.json()["verification_id"] == created["verification_id"]


def test_get_latest_claim_verification_returns_404_when_missing(client) -> None:
    client.post("/claims", json=_claim_payload())

    response = client.get("/claims/claim_123/verification")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VERIFICATION_RESULT_NOT_FOUND"


def test_list_verification_results_supports_filters(client) -> None:
    payload = _claim_payload()
    payload["risk_level"] = "low"
    client.post("/claims", json=payload)
    client.post("/claims/claim_123/verify")

    response = client.get(
        "/verification-results",
        params={
            "claim_id": "claim_123",
            "decision": "requires_human_review",
            "verification_status": "requires_human_review",
            "verification_level": "L1_schema_valid",
        },
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_verify_claim_returns_404_for_unknown_claim(client) -> None:
    response = client.post("/claims/missing/verify")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CLAIM_NOT_FOUND"
