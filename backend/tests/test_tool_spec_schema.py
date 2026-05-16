from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import ConfidenceBand, RiskLevel, VerificationLevel
from app.schemas.safety_policy import SafetyPolicy
from app.schemas.tool_spec import ToolSpec
from app.schemas.verification import VerificationResult
from app.schemas.workflow_spec import WorkflowSpec
from tests.helpers import risk_assessment


def test_compiles_valid_tool_spec_shape() -> None:
    spec = ToolSpec(
        tool_id="vercel-cli",
        name="Vercel CLI",
        interfaces=["cli"],
        capabilities=["create_preview_deployment"],
        commands=[
            {
                "name": "vercel --prod",
                "summary": "Create a production deployment.",
                "risk_level": RiskLevel.HIGH,
                "side_effects": ["creates_remote_deployment"],
                "requires_confirmation": True,
            }
        ],
        auth={"required": True, "methods": ["token"]},
        side_effects=["creates_remote_deployment"],
        risk_profile={
            "reads_private_data": False,
            "mutates_local_state": False,
            "mutates_remote_state": True,
            "can_delete_resources": False,
            "can_incur_cost": True,
            "exposes_secrets": False,
            "default_risk_level": RiskLevel.HIGH,
        },
        examples=[{"title": "Preview deployment", "example": "vercel"}],
        workflows=["deploy-nextjs-vercel-preview"],
        failure_modes=[
            {
                "condition": "Authentication missing",
                "recovery_steps": ["Run `vercel login`", "Retry deployment"],
            }
        ],
        recovery_steps=["Verify project linkage before retrying."],
        provenance={
            "source_claim_ids": ["claim_123"],
            "source_evidence_ids": ["ev_123"],
            "compiled_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "compiled_by": "canon-orchestrator",
            "verification_level": VerificationLevel.L2_SOURCE_VERIFIED,
        },
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
    )

    assert spec.commands[0].requires_confirmation is True


def test_rejects_mismatched_provenance_verification_level() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(
            tool_id="vercel-cli",
            name="Vercel CLI",
            interfaces=["cli"],
            capabilities=["create_preview_deployment"],
            auth={"required": True, "methods": ["token"]},
            risk_profile={"default_risk_level": RiskLevel.HIGH},
            provenance={
                "source_claim_ids": ["claim_123"],
                "source_evidence_ids": ["ev_123"],
                "compiled_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "compiled_by": "canon-orchestrator",
                "verification_level": VerificationLevel.L1_SCHEMA_VALID,
            },
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        )


def test_accepts_valid_workflow_spec() -> None:
    workflow = WorkflowSpec(
        workflow_id="deploy-nextjs-vercel-preview",
        goal="Deploy a Next.js app to preview.",
        tool_ids=["vercel-cli"],
        steps=[
            {
                "step_id": "step_1",
                "action": "verify_project",
                "command": "vercel project ls",
                "risk_level": RiskLevel.LOW,
                "side_effects": [],
                "requires_confirmation": False,
            }
        ],
        provenance={
            "source_claim_ids": ["claim_123"],
            "source_evidence_ids": ["ev_123"],
            "compiled_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "compiled_by": "canon-orchestrator",
            "verification_level": VerificationLevel.L3_RUNTIME_VERIFIED,
        },
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
    )

    assert workflow.steps[0].command == "vercel project ls"


def test_default_safety_policy_blocks_high_risk_auto_execution() -> None:
    policy = SafetyPolicy.default()

    high_rule = next(rule for rule in policy.rules if rule.risk_level == RiskLevel.HIGH)
    critical_rule = next(rule for rule in policy.rules if rule.risk_level == RiskLevel.CRITICAL)

    assert high_rule.auto_execute_allowed is False
    assert critical_rule.requires_human_confirmation is True


def test_accepts_verification_result() -> None:
    result = VerificationResult(
        verification_id="ver_123",
        claim_id="claim_123",
        decision="pending_more_evidence",
        verification_status="pending",
        verification_level="L1_schema_valid",
        confidence=0.4,
        confidence_band=ConfidenceBand.LOW,
        confidence_breakdown=ConfidenceBreakdown(
            score=0.4,
            band=ConfidenceBand.LOW,
            components=[],
            caps_applied=[],
            penalties=[],
        ),
        risk_assessment=risk_assessment(RiskLevel.MEDIUM),
        reason_codes=["MISSING_EVIDENCE"],
        reasons=["Missing trusted source evidence."],
        verified_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )

    assert result.confidence == pytest.approx(0.4)
    assert result.confidence_band == ConfidenceBand.LOW
