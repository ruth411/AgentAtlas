"""Stage 3 invariants: verification level capping, idempotency, schema guards,
submit-time policy enforcement, and the auto-accept path for high-risk claims
with strong evidence.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.orchestrator import CanonOrchestrator
from tests.helpers import valid_sha256


@pytest.fixture
def client(tmp_path):
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru-test.db'}")
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _evidence(
    *,
    evidence_id: str,
    evidence_type: EvidenceType,
    source_uri: str,
    trust_level: TrustLevel = TrustLevel.HIGH,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "source_uri": source_uri,
        "excerpt": "Captured evidence excerpt.",
        "hash": valid_sha256(evidence_id),
        "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        "trust_level": trust_level,
    }


def _claim(
    *,
    claim_id: str = "claim_high_risk",
    subject: str = "gh repo delete",
    statement: str = "`gh repo delete` deletes a GitHub repository.",
    tool_id: str = "github-cli",
    risk_level: RiskLevel = RiskLevel.CRITICAL,
    evidence: list[dict] | None = None,
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        submitted_by="cli-agent",
        evidence=evidence
        or [
            _evidence(
                evidence_id=f"ev_{claim_id}_docs",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
            ),
            _evidence(
                evidence_id=f"ev_{claim_id}_source",
                evidence_type=EvidenceType.SOURCE_CODE,
                source_uri="https://github.com/cli/cli/blob/trunk/pkg/cmd/repo/delete/delete.go",
            ),
        ],
        risk_level=risk_level,
        verification_status=VerificationStatus.PENDING,
        confidence=None,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )


def test_orchestrator_caps_verification_level_at_l2_even_with_sandbox_evidence(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")
    claim = _claim(
        claim_id="claim_l2_cap",
        subject="git status",
        statement="`git status` reads working tree status.",
        tool_id="git",
        risk_level=RiskLevel.LOW,
        evidence=[
            _evidence(
                evidence_id="ev_sandbox",
                evidence_type=EvidenceType.SANDBOX_EXECUTION,
                source_uri="local://sandbox/git-status.json",
            ),
            _evidence(
                evidence_id="ev_docs",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://git-scm.com/docs/git-status",
            ),
        ],
    )

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.verification_level == VerificationLevel.L2_SOURCE_VERIFIED


def test_orchestrator_accepts_high_risk_claim_with_two_trusted_evidence_streams(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")
    claim = _claim()

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.ACCEPTED
    assert result.verification_status == VerificationStatus.ACCEPTED
    assert result.confidence >= 0.85
    assert result.verification_level == VerificationLevel.L2_SOURCE_VERIFIED


def test_verify_endpoint_is_idempotent_after_first_result_even_when_claim_stays_pending(client) -> None:
    payload = {
        "claim_id": "claim_idem",
        "claim_type": "cli_command_exists",
        "subject": "git status",
        "statement": "`git status` shows the working tree status.",
        "tool_id": "git",
        "submitted_by": "cli-agent",
        "evidence": [
            {
                "evidence_id": "ev_idem",
                "evidence_type": "official_docs",
                "source_uri": "https://git-scm.com/docs/git-status",
                "excerpt": "Official git status documentation.",
                "hash": valid_sha256("ev_idem"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc).isoformat(),
                "trust_level": "high",
            }
        ],
        "risk_level": "low",
    }
    client.post("/claims", json=payload)

    first = client.post("/claims/claim_idem/verify").json()
    second = client.post("/claims/claim_idem/verify").json()
    third = client.post("/claims/claim_idem/verify").json()

    assert first["verification_id"] == second["verification_id"] == third["verification_id"]

    listing = client.get("/verification-results", params={"claim_id": "claim_idem"}).json()
    assert len(listing) == 1


def test_post_claim_rejects_rejected_primary_evidence_at_submit(client) -> None:
    payload = {
        "claim_id": "claim_weak",
        "claim_type": "cli_command_exists",
        "subject": "git status",
        "statement": "`git status` reads working tree.",
        "tool_id": "git",
        "submitted_by": "cli-agent",
        "evidence": [
            {
                "evidence_id": "ev_weak",
                "evidence_type": "cli_help_output",
                "source_uri": "llm://memory",
                "excerpt": "I think it shows status.",
                "hash": valid_sha256("ev_weak"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc).isoformat(),
                "trust_level": "high",
            }
        ],
        "risk_level": "low",
    }

    response = client.post("/claims", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REJECTED_PRIMARY_EVIDENCE"
    assert response.json()["error"]["details"]["violations"]


def test_post_claim_rejects_stackoverflow_source_at_submit(client) -> None:
    payload = {
        "claim_id": "claim_stack",
        "claim_type": "cli_command_exists",
        "subject": "git status",
        "statement": "`git status` reads working tree.",
        "tool_id": "git",
        "submitted_by": "cli-agent",
        "evidence": [
            {
                "evidence_id": "ev_stack",
                "evidence_type": "cli_help_output",
                "source_uri": "https://stackoverflow.com/questions/123/git-status",
                "excerpt": "An answer about git status.",
                "hash": valid_sha256("ev_stack"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc).isoformat(),
                "trust_level": "high",
            }
        ],
        "risk_level": "low",
    }

    response = client.post("/claims", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REJECTED_PRIMARY_EVIDENCE"


def test_claim_schema_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(
            claim_id="claim_naive",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="git status",
            statement="`git status` shows the working tree status.",
            tool_id="git",
            submitted_by="cli-agent",
            evidence=[
                Evidence(
                    evidence_id="ev_1",
                    evidence_type=EvidenceType.CLI_HELP_OUTPUT,
                    source_uri="local://git-status-help.txt",
                    excerpt="Show the working tree status.",
                    hash=valid_sha256("ev_1"),
                    captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
                    trust_level=TrustLevel.HIGH,
                )
            ],
            risk_level=RiskLevel.LOW,
            created_at=datetime(2026, 5, 14),
        )


def test_evidence_schema_rejects_naive_captured_at() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev_naive",
            evidence_type=EvidenceType.CLI_HELP_OUTPUT,
            source_uri="local://git-status-help.txt",
            excerpt="Show the working tree status.",
            hash=valid_sha256("ev_naive"),
            captured_at=datetime(2026, 5, 14),
            trust_level=TrustLevel.HIGH,
        )


def test_evidence_schema_rejects_future_captured_at() -> None:
    future = datetime.now(timezone.utc) + timedelta(days=30)
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev_future",
            evidence_type=EvidenceType.CLI_HELP_OUTPUT,
            source_uri="local://git-status-help.txt",
            excerpt="Show the working tree status.",
            hash=valid_sha256("ev_future"),
            captured_at=future,
            trust_level=TrustLevel.HIGH,
        )


def test_evidence_policy_blog_subdomain_uses_url_parsing_not_substring() -> None:
    """`weblog.example.com` is rejected (hostname label match); the substring
    'blog' appearing inside a different word should not falsely reject a
    legitimate source like `https://example.com/api/blogger` if there is no
    hostname label match."""
    from app.services.evidence_policy import _is_rejected_source

    assert _is_rejected_source("https://blog.example.com/post") is True
    assert _is_rejected_source("https://weblog.example.com/post") is True
    assert _is_rejected_source("https://example.com/api/blogger") is False
    assert _is_rejected_source("https://docs.example.com/page") is False


def test_post_claim_server_assigns_created_at_and_ignores_client_supplied(client) -> None:
    """Clients cannot set created_at; the field is system-managed."""
    payload = {
        "claim_id": "claim_srv_time",
        "claim_type": "cli_command_exists",
        "subject": "git status",
        "statement": "`git status` reads working tree.",
        "tool_id": "git",
        "submitted_by": "cli-agent",
        "evidence": [
            {
                "evidence_id": "ev_srv_time",
                "evidence_type": "cli_help_output",
                "source_uri": "local://git-status-help.txt",
                "excerpt": "Show the working tree status.",
                "hash": valid_sha256("ev_srv_time"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc).isoformat(),
                "trust_level": "high",
            }
        ],
        "risk_level": "low",
        "created_at": "1970-01-01T00:00:00+00:00",
    }

    response = client.post("/claims", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CLAIM_SCHEMA"
