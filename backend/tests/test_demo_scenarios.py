"""End-to-end tests for the Stage 0 demo scenarios.

These tests prove that the Stage 3 services correctly classify the five
canonical demo commands defined in `contracts/ayiru_stage_0.v1.json`.
They are intentionally service-level (risk classifier + orchestrator) rather
than dependent on the Stage 9 query surface, which does not yet exist.
"""

from datetime import datetime, timezone

import pytest

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationStatus,
)
from app.services.claim_store import ClaimStore
from app.services.orchestrator import CanonOrchestrator
from app.services.risk_classifier import classify_action
from tests.helpers import valid_sha256


def _store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


def _claim(
    *,
    claim_id: str,
    claim_type: ClaimType,
    subject: str,
    statement: str,
    tool_id: str,
    risk_level: RiskLevel,
    evidence_type: EvidenceType = EvidenceType.OFFICIAL_DOCS,
    source_uri: str | None = None,
) -> KnowledgeClaim:
    if source_uri is None:
        source_uri = {
            "github-cli": "https://docs.github.com/cli/manual/gh_repo_delete",
            "docker": "https://docs.docker.com/reference/cli/docker/system/prune/",
            "vercel-cli": "https://vercel.com/docs/cli/deploy",
            "git": "https://git-scm.com/docs/git-status",
            "openai-api": "https://platform.openai.com/docs/api-reference/authentication",
        }[tool_id]

    evidence = [
        {
            "evidence_id": f"ev_{claim_id}",
            "evidence_type": evidence_type,
            "source_uri": source_uri,
            "excerpt": "Captured evidence excerpt.",
            "hash": valid_sha256(f"ev_{claim_id}"),
            "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "trust_level": TrustLevel.HIGH,
        }
    ]
    if tool_id == "git" and subject == "git status":
        evidence.append(
            {
                "evidence_id": f"ev_{claim_id}_source",
                "evidence_type": EvidenceType.SOURCE_CODE,
                "source_uri": "https://github.com/git/git/blob/master/wt-status.c",
                "excerpt": "git status source implementation.",
                "hash": valid_sha256(f"ev_{claim_id}_source"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "trust_level": TrustLevel.HIGH,
            }
        )
    if tool_id == "openai-api":
        evidence.append(
            {
                "evidence_id": f"ev_{claim_id}_schema",
                "evidence_type": EvidenceType.OPENAPI_SCHEMA,
                "source_uri": "https://api.openai.com/v1/openapi.json",
                "excerpt": "OpenAI API authentication schema evidence.",
                "hash": valid_sha256(f"ev_{claim_id}_schema"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "trust_level": TrustLevel.HIGH,
            }
        )

    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=claim_type,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        submitted_by="cli-agent",
        evidence=evidence,
        risk_level=risk_level,
        verification_status=VerificationStatus.PENDING,
        confidence=None,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("gh repo delete my-org/my-repo --yes", RiskLevel.CRITICAL),
        ("docker system prune -a", RiskLevel.CRITICAL),
        ("vercel --prod", RiskLevel.HIGH),
        ("git status", RiskLevel.LOW),
        ("git push --force origin main", RiskLevel.CRITICAL),
        ("git reset --hard origin/main", RiskLevel.CRITICAL),
        ("rm -rf /tmp/scratch", RiskLevel.CRITICAL),
        ("terraform destroy", RiskLevel.CRITICAL),
        ("kubectl delete namespace prod", RiskLevel.CRITICAL),
        ("git log --oneline", RiskLevel.LOW),
    ],
)
def test_risk_classifier_handles_known_dangerous_and_safe_commands(
    subject: str, expected: RiskLevel
) -> None:
    assessment = classify_action(subject)

    assert assessment.risk_level == expected
    if expected in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}:
        assert assessment.safe_to_auto_execute is False
        assert assessment.requires_confirmation is True
    else:
        assert assessment.safe_to_auto_execute is True
        assert assessment.requires_confirmation is False


@pytest.mark.parametrize(
    "subject,unexpected",
    [
        ("git merger-tool", RiskLevel.CRITICAL),
        ("redeploy-status", RiskLevel.HIGH),
    ],
)
def test_risk_classifier_uses_word_boundaries(subject: str, unexpected: RiskLevel) -> None:
    assessment = classify_action(subject)

    assert assessment.risk_level != unexpected
    assert assessment.matched_rule_ids == []


def test_demo_scenario_gh_repo_delete_routes_to_human_review(tmp_path) -> None:
    claim = _claim(
        claim_id="demo_gh_delete",
        claim_type=ClaimType.DESTRUCTIVE_ACTION,
        subject="gh repo delete my-org/my-repo --yes",
        statement="Deletes a GitHub repository and is irreversible.",
        tool_id="github-cli",
        risk_level=RiskLevel.CRITICAL,
    )

    result = CanonOrchestrator(_store(tmp_path)).verify_claim(claim)

    assert result.decision == OrchestratorDecision.REQUIRES_HUMAN_REVIEW
    assert result.reason_codes == ["HIGH_RISK_NEEDS_REVIEW"]


def test_demo_scenario_docker_system_prune_routes_to_human_review(tmp_path) -> None:
    claim = _claim(
        claim_id="demo_docker_prune",
        claim_type=ClaimType.DESTRUCTIVE_ACTION,
        subject="docker system prune -a",
        statement="Removes unused images, containers, networks, and build cache.",
        tool_id="docker",
        risk_level=RiskLevel.CRITICAL,
    )

    result = CanonOrchestrator(_store(tmp_path)).verify_claim(claim)

    assert result.decision == OrchestratorDecision.REQUIRES_HUMAN_REVIEW


def test_demo_scenario_vercel_prod_routes_to_human_review(tmp_path) -> None:
    claim = _claim(
        claim_id="demo_vercel_prod",
        claim_type=ClaimType.SIDE_EFFECT,
        subject="vercel --prod",
        statement="Creates a production Vercel deployment.",
        tool_id="vercel-cli",
        risk_level=RiskLevel.HIGH,
    )

    result = CanonOrchestrator(_store(tmp_path)).verify_claim(claim)

    assert result.decision == OrchestratorDecision.REQUIRES_HUMAN_REVIEW


def test_demo_scenario_git_status_accepts_at_l2(tmp_path) -> None:
    claim = _claim(
        claim_id="demo_git_status",
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="`git status` shows the working tree status.",
        tool_id="git",
        risk_level=RiskLevel.LOW,
    )

    result = CanonOrchestrator(_store(tmp_path)).verify_claim(claim)

    assert result.decision == OrchestratorDecision.ACCEPTED
    assert result.verification_status == VerificationStatus.ACCEPTED
    assert result.verification_level == "L2_source_verified"


def test_demo_scenario_openai_auth_requirement_accepts_with_strong_evidence(tmp_path) -> None:
    claim = _claim(
        claim_id="demo_openai_auth",
        claim_type=ClaimType.AUTH_REQUIREMENT,
        subject="OpenAI API model call",
        statement="Calling OpenAI model endpoints requires an API key and may incur cost.",
        tool_id="openai-api",
        risk_level=RiskLevel.MEDIUM,
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri="https://platform.openai.com/docs/api-reference/authentication",
    )

    result = CanonOrchestrator(_store(tmp_path)).verify_claim(claim)

    assert result.decision == OrchestratorDecision.ACCEPTED
    assert result.verification_status == VerificationStatus.ACCEPTED
