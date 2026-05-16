from datetime import datetime, timezone

import pytest

from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown
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
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore, DuplicateEvidenceError
from tests.helpers import risk_assessment, valid_sha256


def _breakdown(score: float, band: ConfidenceBand) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(score=score, band=band, components=[], caps_applied=[], penalties=[])


def _claim(
    claim_id: str = "claim_123",
    tool_id: str = "github-cli",
    *,
    statement: str = "The GitHub CLI command `gh repo delete` deletes a repository.",
    risk_level: RiskLevel = RiskLevel.CRITICAL,
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="gh repo delete",
        statement=statement,
        tool_id=tool_id,
        submitted_by="cli-agent",
        evidence=[
            {
                "evidence_id": f"ev_{claim_id}",
                "evidence_type": EvidenceType.CLI_HELP_OUTPUT,
                "source_uri": "local://gh-repo-delete-help.txt",
                "excerpt": "Delete a repository on GitHub.",
                "hash": valid_sha256(f"ev_{claim_id}"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "trust_level": TrustLevel.HIGH,
            }
        ],
        risk_level=risk_level,
        verification_status=VerificationStatus.PENDING,
        confidence=None,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )


def test_claims_and_evidence_survive_store_reinitialization(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'agentatlas.db'}"
    first_store = ClaimStore(database_url=database_url)
    expected = first_store.create(_claim())
    second_store = ClaimStore(database_url=database_url)

    actual = second_store.get("claim_123")
    assert actual is not None
    assert actual.model_dump(mode="json") == expected.model_dump(mode="json")


def test_evidence_records_are_queryable_independently(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim())

    evidence = store.list_evidence(claim_id="claim_123")

    assert len(evidence) == 1
    assert evidence[0].evidence_id == "ev_claim_123"
    assert evidence[0].evidence_type == EvidenceType.CLI_HELP_OUTPUT


def test_store_normalizes_evidence_trust_before_persistence(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")

    created = store.create(_claim())

    assert created.evidence[0].trust_level == TrustLevel.MEDIUM
    assert store.get("claim_123").evidence[0].trust_level == TrustLevel.MEDIUM


def test_claims_can_be_filtered_by_tool_status_risk_and_submitter(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim(claim_id="claim_123", tool_id="github-cli"))
    store.create(_claim(claim_id="claim_456", tool_id="git"))

    github_claims = store.list(
        tool_id="github-cli",
        verification_status=VerificationStatus.PENDING,
        risk_level=RiskLevel.CRITICAL,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        submitted_by="cli-agent",
    )

    assert [claim.claim_id for claim in github_claims] == ["claim_123"]


def test_claims_can_be_filtered_by_classified_risk_after_verification(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim(claim_id="claim_123", tool_id="github-cli"))
    result = VerificationResult(
        verification_id="ver_123",
        claim_id="claim_123",
        decision=OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
        verification_status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
        verification_level=VerificationLevel.L1_SCHEMA_VALID,
        confidence=0.5,
        confidence_band=ConfidenceBand.LOW,
        confidence_breakdown=_breakdown(0.5, ConfidenceBand.LOW),
        risk_assessment=risk_assessment(RiskLevel.CRITICAL),
        reason_codes=["UNDERSTATED_RISK"],
        reasons=["Submitted risk is lower than classified risk."],
        verified_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    store.save_verification_result(result)

    critical_claims = store.list(risk_level_classified=RiskLevel.CRITICAL)
    low_claims = store.list(risk_level_classified=RiskLevel.LOW)

    assert [claim.claim_id for claim in critical_claims] == ["claim_123"]
    assert low_claims == []


def test_clear_removes_claims_and_attached_evidence(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim())

    store.clear()

    assert store.list() == []
    assert store.list_evidence() == []


def test_duplicate_evidence_id_rolls_back_entire_claim_insert(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim(claim_id="claim_123"))

    duplicate_evidence_claim = _claim(claim_id="claim_456")
    duplicate_evidence_claim.evidence[0].evidence_id = "ev_claim_123"

    with pytest.raises(DuplicateEvidenceError):
        store.create(duplicate_evidence_claim)

    assert [claim.claim_id for claim in store.list()] == ["claim_123"]
    assert [evidence.evidence_id for evidence in store.list_evidence()] == ["ev_claim_123"]


def test_store_finds_semantic_duplicates_without_requiring_same_id(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    existing = _claim(claim_id="claim_123")
    incoming = _claim(claim_id="claim_456")
    store.create(existing)

    duplicates = store.find_semantic_duplicates(incoming)

    assert [claim.claim_id for claim in duplicates] == ["claim_123"]


def test_store_finds_semantic_duplicates_case_and_whitespace_insensitive(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    existing = _claim(claim_id="claim_123")
    incoming = _claim(claim_id="claim_456")
    incoming.subject = "  GH   REPO   DELETE  "
    store.create(existing)

    duplicates = store.find_semantic_duplicates(incoming)

    assert [claim.claim_id for claim in duplicates] == ["claim_123"]


def test_store_finds_conflicts_for_same_subject_with_different_risk_or_statement(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    existing = _claim(claim_id="claim_123")
    incoming = _claim(
        claim_id="claim_456",
        statement="The GitHub CLI command `gh repo delete` lists repositories.",
        risk_level=RiskLevel.LOW,
    )
    store.create(existing)

    conflicts = store.find_conflicts(incoming)

    assert [claim.claim_id for claim in conflicts] == ["claim_123"]


def test_store_gets_evidence_by_id(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim())

    evidence = store.get_evidence("ev_claim_123")

    assert evidence is not None
    assert evidence.evidence_id == "ev_claim_123"


def test_store_persists_verification_result_and_updates_claim(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim())
    result = VerificationResult(
        verification_id="ver_123",
        claim_id="claim_123",
        decision=OrchestratorDecision.ACCEPTED,
        verification_status=VerificationStatus.ACCEPTED,
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        confidence=0.85,
        confidence_band=ConfidenceBand.HIGH,
        confidence_breakdown=_breakdown(0.85, ConfidenceBand.HIGH),
        risk_assessment=risk_assessment(RiskLevel.CRITICAL),
        reason_codes=["TRUSTED_EVIDENCE_NO_CONFLICTS"],
        reasons=["Claim has trusted evidence and no detected conflicts."],
        verified_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )

    store.save_verification_result(result)

    stored_result = store.get_latest_verification_result("claim_123")
    stored_claim = store.get("claim_123")
    assert stored_result is not None
    assert stored_result.model_dump(mode="json") == result.model_dump(mode="json")
    assert stored_claim is not None
    assert stored_claim.verification_status == VerificationStatus.ACCEPTED
    assert stored_claim.confidence == 0.85
    assert stored_claim.confidence_band == ConfidenceBand.HIGH
    assert stored_claim.risk_level_classified == RiskLevel.CRITICAL
    assert stored_claim.risk_assessment == result.risk_assessment


def test_store_lists_verification_results_with_filters(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(_claim())
    result = VerificationResult(
        verification_id="ver_123",
        claim_id="claim_123",
        decision=OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
        verification_status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
        verification_level=VerificationLevel.L1_SCHEMA_VALID,
        confidence=0.5,
        confidence_band=ConfidenceBand.LOW,
        confidence_breakdown=_breakdown(0.5, ConfidenceBand.LOW),
        risk_assessment=risk_assessment(RiskLevel.CRITICAL),
        reason_codes=["UNDERSTATED_RISK"],
        reasons=["Submitted risk is lower than classified risk."],
        verified_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    store.save_verification_result(result)

    results = store.list_verification_results(
        claim_id="claim_123",
        decision=OrchestratorDecision.REQUIRES_HUMAN_REVIEW,
        verification_status=VerificationStatus.REQUIRES_HUMAN_REVIEW,
        verification_level=VerificationLevel.L1_SCHEMA_VALID,
    )

    assert [item.verification_id for item in results] == ["ver_123"]
