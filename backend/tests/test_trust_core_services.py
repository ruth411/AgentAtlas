from datetime import datetime, timezone

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.schemas.risk import RiskDimension
from app.services.claim_store import ClaimStore
from app.services.confidence_scorer import score_claim_confidence
from app.services.orchestrator import CanonOrchestrator
from app.services.risk_classifier import classify_action
from tests.helpers import valid_sha256


def _claim(
    *,
    claim_id: str = "claim_123",
    subject: str = "git status",
    statement: str = "`git status` shows the working tree status.",
    risk_level: RiskLevel = RiskLevel.LOW,
    evidence_type: EvidenceType = EvidenceType.OFFICIAL_DOCS,
    source_uri: str = "https://git-scm.com/docs/git-status",
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject=subject,
        statement=statement,
        tool_id="git",
        submitted_by="cli-agent",
        evidence=[
            {
                "evidence_id": f"ev_{claim_id}",
                "evidence_type": evidence_type,
                "source_uri": source_uri,
                "excerpt": "Show the working tree status.",
                "hash": valid_sha256(f"ev_{claim_id}"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "trust_level": TrustLevel.HIGH,
            }
        ],
        risk_level=risk_level,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )


def test_risk_classifier_marks_dangerous_commands_as_critical() -> None:
    assessment = classify_action("gh repo delete", "Deletes a GitHub repository.")

    assert assessment.risk_level == RiskLevel.CRITICAL
    assert assessment.safe_to_auto_execute is False
    assert assessment.requires_confirmation is True
    assert RiskDimension.DESTRUCTIVE in assessment.dimensions
    assert assessment.matched_rule_ids == ["gh.repo_delete"]


def test_confidence_scoring_caps_high_risk_single_source_claims() -> None:
    claim = _claim(
        subject="gh repo delete",
        statement="Deletes a GitHub repository.",
        risk_level=RiskLevel.CRITICAL,
    )

    assert score_claim_confidence(claim) <= 0.5


def test_orchestrator_accepts_low_risk_source_verified_claim(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    claim = _claim()
    claim.evidence.append(
        Evidence(
            evidence_id="ev_claim_123_source",
            evidence_type=EvidenceType.SOURCE_CODE,
            source_uri="https://github.com/git/git/blob/master/wt-status.c",
            excerpt="git status source implementation.",
            hash=valid_sha256("ev_claim_123_source"),
            captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            trust_level=TrustLevel.HIGH,
        )
    )

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.ACCEPTED
    assert result.verification_status == "accepted"
    assert result.verification_level == "L2_source_verified"
    assert result.verification_id.startswith("ver_")
    assert result.reason_codes == ["TRUSTED_EVIDENCE_NO_CONFLICTS"]
    assert result.risk_assessment.risk_level == RiskLevel.LOW
    assert result.risk_assessment.matched_rule_ids == ["git.status"]


def test_orchestrator_does_not_accept_low_band_claim(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    claim = _claim()

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.PENDING_MORE_EVIDENCE
    assert result.verification_level == "L2_source_verified"
    assert result.reason_codes == ["INSUFFICIENT_CONFIDENCE_FOR_ACCEPTANCE"]


def test_orchestrator_requires_more_evidence_when_claim_has_no_evidence(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    claim = _claim()
    claim.evidence = []

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.PENDING_MORE_EVIDENCE
    assert result.confidence == 0.0


def test_orchestrator_rejects_weak_primary_evidence(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    claim = _claim(source_uri="llm://memory")

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.REJECTED


def test_orchestrator_detects_duplicate_claims(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    existing = _claim(claim_id="claim_123")
    incoming = _claim(claim_id="claim_456")
    store.create(existing)

    result = CanonOrchestrator(store).verify_claim(incoming)

    assert result.decision == OrchestratorDecision.DUPLICATE


def test_orchestrator_detects_conflicting_claims(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    existing = _claim(claim_id="claim_123")
    incoming = _claim(
        claim_id="claim_456",
        statement="`git status` deletes local files.",
        risk_level=RiskLevel.CRITICAL,
    )
    store.create(existing)

    result = CanonOrchestrator(store).verify_claim(incoming)

    assert result.decision == OrchestratorDecision.CONFLICT_DETECTED
    assert result.verification_status == "conflict_detected"


def test_orchestrator_challenges_understated_risk(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    claim = _claim(
        subject="gh repo delete",
        statement="Deletes a GitHub repository.",
        risk_level=RiskLevel.LOW,
    )
    claim.tool_id = "github-cli"

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.REQUIRES_HUMAN_REVIEW
    assert result.risk_assessment.risk_level == RiskLevel.CRITICAL
    assert result.risk_assessment.matched_rule_ids == ["gh.repo_delete"]


def test_understated_risk_breakdown_components_reconcile_to_capped_score(tmp_path) -> None:
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    claim = _claim(
        subject="gh repo delete",
        statement="Deletes a GitHub repository.",
        risk_level=RiskLevel.LOW,
    )
    claim.tool_id = "github-cli"
    claim.evidence = [
        Evidence(
            evidence_id="ev_docs",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
            excerpt="GitHub CLI repo delete documentation.",
            hash=valid_sha256("ev_docs"),
            captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            trust_level=TrustLevel.HIGH,
        ),
        Evidence(
            evidence_id="ev_source",
            evidence_type=EvidenceType.SOURCE_CODE,
            source_uri="https://github.com/cli/cli/blob/trunk/pkg/cmd/repo/delete/delete.go",
            excerpt="GitHub CLI repo delete source.",
            hash=valid_sha256("ev_source"),
            captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            trust_level=TrustLevel.HIGH,
        ),
    ]

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.confidence == 0.5
    assert any(
        component.source == "cap:understated_risk" and component.delta < 0
        for component in result.confidence_breakdown.components
    )
    assert round(sum(c.delta for c in result.confidence_breakdown.components), 4) == result.confidence
