from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import ClaimType, EvidenceType, RiskLevel, TrustLevel, VerificationStatus
from tests.helpers import valid_sha256


def _evidence_payload() -> dict:
    return {
        "evidence_id": "ev_123",
        "evidence_type": EvidenceType.CLI_HELP_OUTPUT,
        "source_uri": "local://gh-repo-delete-help.txt",
        "excerpt": "Delete a repository on GitHub.",
        "hash": valid_sha256("ev_123"),
        "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        "trust_level": TrustLevel.HIGH,
    }


def test_accepts_valid_claim() -> None:
    claim = KnowledgeClaim(
        claim_id="claim_123",
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="gh repo delete",
        statement="The GitHub CLI command `gh repo delete` deletes a repository.",
        tool_id="github-cli",
        submitted_by="cli-agent",
        evidence=[_evidence_payload()],
        risk_level=RiskLevel.CRITICAL,
        verification_status=VerificationStatus.PENDING,
        confidence=0.6,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )

    assert claim.tool_id == "github-cli"
    assert claim.evidence[0].evidence_type == EvidenceType.CLI_HELP_OUTPUT


def test_rejects_claim_without_required_fields() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(
            claim_id="claim_123",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="",
            tool_id="github-cli",
            submitted_by="cli-agent",
            evidence=[],
            risk_level=RiskLevel.CRITICAL,
            created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        )


def test_rejects_claim_with_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(
            claim_id="claim_123",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="The GitHub CLI command `gh repo delete` deletes a repository.",
            tool_id="github-cli",
            submitted_by="cli-agent",
            evidence=[_evidence_payload()],
            risk_level=RiskLevel.CRITICAL,
            confidence=1.2,
            created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        )
