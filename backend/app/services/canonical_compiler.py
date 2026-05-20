from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from functools import cache
import re

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    RISK_ORDER,
    VERIFICATION_LEVEL_ORDER,
    ClaimType,
    RiskLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.risk import RiskDimension
from app.schemas.tool_spec import (
    AuthRequirement,
    CommandSpec,
    Provenance,
    PublicationIssue,
    RiskProfile,
    ToolSpec,
)
from app.schemas.verification import VerificationResult
from app.schemas.workflow_spec import WorkflowSpec, WorkflowStep
from app.services.claim_store import (
    ClaimStore,
    canonical_content_hash,
    canonical_spec_hash,
    canonical_spec_json,
)
from app.services.contract_paths import contract_path


_STAGE_0_CONTRACT = contract_path("agentatlas_stage_0.v1.json")
_WORKFLOW_SUBJECT = re.compile(r"^(?P<workflow_id>[^:]+)::(?P<step_number>[0-9]+)::(?P<action>.+)$")

class CanonicalPublicationError(ValueError):
    """Raised when a canonical spec cannot be published without inventing truth."""


@dataclass(frozen=True)
class CompiledCanonicalSpec:
    spec: ToolSpec | WorkflowSpec
    spec_json: str
    spec_hash: str
    content_hash: str


class ToolSpecCompiler:
    def __init__(self, store: ClaimStore) -> None:
        self._store = store

    def compile(
        self,
        tool_id: str,
        compiled_at: datetime | None = None,
        compiled_by: str = "canon-compiler",
    ) -> CompiledCanonicalSpec:
        claims = _sort_claims(
            self._store.list(
                tool_id=tool_id,
                verification_status=VerificationStatus.ACCEPTED,
            )
        )
        if not claims:
            raise CanonicalPublicationError(
                f"Tool '{tool_id}' has no accepted claims eligible for publication."
            )

        tool_metadata = _stage_0_tool_metadata(tool_id)
        if tool_metadata is None:
            raise CanonicalPublicationError(
                f"Tool '{tool_id}' is not present in the Stage 0 tool contract."
            )

        compiled_at = _compiled_at(compiled_at)
        issues: list[PublicationIssue] = []
        latest_results = _latest_results(self._store, claims, issues)
        verification_level = _minimum_verification_level(latest_results)
        source_claim_ids = sorted(claim.claim_id for claim in claims)
        source_evidence_ids = _source_evidence_ids(claims)

        command_claims = [claim for claim in claims if claim.claim_type == ClaimType.CLI_COMMAND_EXISTS]
        if not command_claims:
            issues.append(
                PublicationIssue(
                    code="missing_command_claim",
                    message="No accepted CLI command claims are available for this tool.",
                    source_claim_ids=source_claim_ids,
                )
            )

        auth_claims = [claim for claim in claims if claim.claim_type == ClaimType.AUTH_REQUIREMENT]
        auth = AuthRequirement(required=None, methods=[])
        if not auth_claims:
            issues.append(
                PublicationIssue(
                    code="missing_auth_claim",
                    message="No accepted auth requirement claim is available for this tool.",
                    source_claim_ids=source_claim_ids,
                )
            )
        else:
            issues.append(
                PublicationIssue(
                    code="unstructured_auth_claim",
                    message=(
                        "Accepted auth claims exist, but Stage 6 does not parse prose into "
                        "a boolean auth requirement."
                    ),
                    source_claim_ids=[claim.claim_id for claim in auth_claims],
                )
            )

        side_effect_claims = [
            claim
            for claim in claims
            if claim.claim_type in {ClaimType.SIDE_EFFECT, ClaimType.DESTRUCTIVE_ACTION}
        ]
        if side_effect_claims:
            issues.append(
                PublicationIssue(
                    code="unstructured_side_effect_claim",
                    message=(
                        "Accepted side-effect claims exist, but Stage 6 does not parse prose "
                        "subjects into canonical side-effect names."
                    ),
                    source_claim_ids=[claim.claim_id for claim in side_effect_claims],
                )
            )
        _add_tool_absence_issues(issues, source_claim_ids, command_claims)

        spec = ToolSpec(
            tool_id=tool_id,
            name=tool_metadata["name"],
            interfaces=sorted(tool_metadata["interfaces"]),
            capabilities=_capabilities(claims),
            commands=[
                CommandSpec(
                    name=claim.subject,
                    summary=claim.statement,
                    risk_level=_claim_risk_level(claim),
                    side_effects=[],
                    requires_confirmation=_requires_confirmation(_claim_risk_level(claim)),
                )
                for claim in command_claims
            ],
            inputs=[],
            outputs=[],
            auth=auth,
            side_effects=[],
            risk_profile=_risk_profile(claims, issues),
            examples=[],
            workflows=[],
            failure_modes=[],
            recovery_steps=[],
            provenance=Provenance(
                source_claim_ids=source_claim_ids,
                source_evidence_ids=source_evidence_ids,
                compiled_at=compiled_at,
                compiled_by=compiled_by,
                verification_level=verification_level,
            ),
            verification_level=verification_level,
            publication_issues=sorted(issues, key=lambda item: (item.code, item.message)),
        )
        spec = spec.model_copy(update={"content_hash": canonical_content_hash(spec)})
        return CompiledCanonicalSpec(
            spec=spec,
            spec_json=canonical_spec_json(spec),
            spec_hash=canonical_spec_hash(spec),
            content_hash=canonical_content_hash(spec),
        )


class WorkflowSpecCompiler:
    def __init__(self, store: ClaimStore) -> None:
        self._store = store

    def compile(
        self,
        workflow_id: str,
        compiled_at: datetime | None = None,
        compiled_by: str = "canon-compiler",
    ) -> CompiledCanonicalSpec:
        candidates = _sort_claims(
            self._store.list(
                verification_status=VerificationStatus.ACCEPTED,
                claim_type=ClaimType.WORKFLOW_STEP,
            )
        )
        considered: list[KnowledgeClaim] = []
        valid_steps: list[tuple[int, KnowledgeClaim, str]] = []
        issues: list[PublicationIssue] = []

        for claim in candidates:
            parsed = _parse_workflow_subject(claim.subject)
            if parsed is None:
                if claim.subject.strip().startswith(f"{workflow_id}::"):
                    considered.append(claim)
                    issues.append(
                        PublicationIssue(
                            code="invalid_workflow_subject",
                            message=(
                                "Workflow step subject must use "
                                "workflow_id::step_number::action."
                            ),
                            source_claim_ids=[claim.claim_id],
                        )
                    )
                continue
            parsed_workflow_id, step_number, action = parsed
            if parsed_workflow_id != workflow_id:
                continue
            considered.append(claim)
            if step_number < 1:
                issues.append(
                    PublicationIssue(
                        code="invalid_workflow_subject",
                        message="Workflow step number must be greater than zero.",
                        source_claim_ids=[claim.claim_id],
                    )
                )
                continue
            valid_steps.append((step_number, claim, action))

        if not considered:
            raise CanonicalPublicationError(
                f"Workflow '{workflow_id}' has no accepted workflow-step claims eligible for publication."
            )
        if not valid_steps:
            raise CanonicalPublicationError(
                f"Workflow '{workflow_id}' has no valid workflow-step claims eligible for publication."
            )

        compiled_at = _compiled_at(compiled_at)
        latest_results = _latest_results(self._store, considered, issues)
        verification_level = _minimum_verification_level(latest_results)
        source_claim_ids = sorted(claim.claim_id for claim in considered)
        source_evidence_ids = _source_evidence_ids(considered)
        valid_steps.sort(key=lambda item: (item[0], item[1].claim_id))
        issues.append(
            PublicationIssue(
                code="missing_workflow_goal",
                message="No accepted structured workflow goal claim exists; goal is unknown.",
                source_claim_ids=source_claim_ids,
            )
        )
        issues.append(
            PublicationIssue(
                code="missing_workflow_step_command",
                message=(
                    "Workflow step claims provide prose statements; Stage 6 does not publish "
                    "them as executable commands."
                ),
                source_claim_ids=[claim.claim_id for _, claim, _ in valid_steps],
            )
        )

        spec = WorkflowSpec(
            workflow_id=workflow_id,
            goal=None,
            tool_ids=sorted({claim.tool_id for _, claim, _ in valid_steps}),
            steps=[
                WorkflowStep(
                    step_id=f"step_{step_number}",
                    action=action,
                    command=None,
                    description=claim.statement,
                    risk_level=_claim_risk_level(claim),
                    side_effects=[],
                    requires_confirmation=_requires_confirmation(_claim_risk_level(claim)),
                )
                for step_number, claim, action in valid_steps
            ],
            provenance=Provenance(
                source_claim_ids=source_claim_ids,
                source_evidence_ids=source_evidence_ids,
                compiled_at=compiled_at,
                compiled_by=compiled_by,
                verification_level=verification_level,
            ),
            verification_level=verification_level,
            publication_issues=sorted(issues, key=lambda item: (item.code, item.message)),
        )
        spec = spec.model_copy(update={"content_hash": canonical_content_hash(spec)})
        return CompiledCanonicalSpec(
            spec=spec,
            spec_json=canonical_spec_json(spec),
            spec_hash=canonical_spec_hash(spec),
            content_hash=canonical_content_hash(spec),
        )


def _compiled_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _sort_claims(claims: list[KnowledgeClaim]) -> list[KnowledgeClaim]:
    return sorted(claims, key=lambda claim: (claim.tool_id, claim.claim_type.value, claim.subject, claim.claim_id))


def _latest_results(
    store: ClaimStore,
    claims: list[KnowledgeClaim],
    issues: list[PublicationIssue],
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    for claim in claims:
        result = store.get_latest_verification_result(claim.claim_id)
        if result is None:
            issues.append(
                PublicationIssue(
                    code="missing_verification_result",
                    message="Accepted claim has no persisted verification result.",
                    source_claim_ids=[claim.claim_id],
                )
            )
            continue
        results.append(result)
    return results


def _minimum_verification_level(results: list[VerificationResult]) -> VerificationLevel:
    if not results:
        return VerificationLevel.L1_SCHEMA_VALID
    return min(
        (result.verification_level for result in results),
        key=lambda item: VERIFICATION_LEVEL_ORDER[item],
    )


def _source_evidence_ids(claims: list[KnowledgeClaim]) -> list[str]:
    return sorted({evidence.evidence_id for claim in claims for evidence in claim.evidence})


def _capabilities(claims: list[KnowledgeClaim]) -> list[str]:
    return sorted({_machine_key(f"{claim.claim_type.value}:{claim.subject}") for claim in claims})


def _machine_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    return normalized or "unknown"


def _claim_risk_level(claim: KnowledgeClaim) -> RiskLevel:
    return claim.risk_level_classified or claim.risk_level


def _requires_confirmation(risk_level: RiskLevel) -> bool:
    return risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}


def _risk_profile(claims: list[KnowledgeClaim], issues: list[PublicationIssue]) -> RiskProfile:
    risk_levels = [_claim_risk_level(claim) for claim in claims]
    default_risk = max(risk_levels, key=lambda item: RISK_ORDER[item])
    dimensions = {
        dimension
        for claim in claims
        if claim.risk_assessment is not None
        for dimension in claim.risk_assessment.dimensions
    }
    missing_risk_claim_ids = [claim.claim_id for claim in claims if claim.risk_assessment is None]
    if missing_risk_claim_ids:
        issues.append(
            PublicationIssue(
                code="missing_risk_assessment",
                message="One or more accepted claims have no classified risk assessment.",
                source_claim_ids=missing_risk_claim_ids,
            )
        )
    return RiskProfile(
        reads_private_data=None,
        mutates_local_state=RiskDimension.LOCAL_MUTATION in dimensions
        or RiskDimension.FILESYSTEM in dimensions,
        mutates_remote_state=RiskDimension.REMOTE_MUTATION in dimensions,
        can_delete_resources=RiskDimension.DESTRUCTIVE in dimensions,
        can_incur_cost=RiskDimension.COST_INCURRING in dimensions,
        exposes_secrets=RiskDimension.SECRET_EXPOSURE in dimensions,
        default_risk_level=default_risk,
    )


def _add_tool_absence_issues(
    issues: list[PublicationIssue],
    source_claim_ids: list[str],
    command_claims: list[KnowledgeClaim],
) -> None:
    for code, message in (
        ("missing_inputs", "No accepted structured input claims are available for this tool."),
        ("missing_outputs", "No accepted structured output claims are available for this tool."),
        ("missing_examples", "No accepted structured example claims are available for this tool."),
        ("missing_failure_modes", "No accepted structured failure-mode claims are available."),
        ("missing_recovery_steps", "No accepted structured recovery-step claims are available."),
    ):
        issues.append(
            PublicationIssue(
                code=code,
                message=message,
                source_claim_ids=source_claim_ids,
            )
        )
    if command_claims:
        issues.append(
            PublicationIssue(
                code="missing_command_side_effects",
                message="Command-level side effects are unknown until structured side-effect claims exist.",
                source_claim_ids=[claim.claim_id for claim in command_claims],
            )
        )
        issues.append(
            PublicationIssue(
                code="derived_capabilities",
                message="Capabilities are deterministic derived keys from accepted claims, not separate capability claims.",
                source_claim_ids=source_claim_ids,
            )
        )
    issues.append(
        PublicationIssue(
            code="interfaces_from_contract",
            message=(
                "Interfaces are taken from the Stage 0 tool contract, not from accepted "
                "claims; no interface_exists claim type exists yet."
            ),
            source_claim_ids=source_claim_ids,
        )
    )


def _parse_workflow_subject(subject: str) -> tuple[str, int, str] | None:
    match = _WORKFLOW_SUBJECT.match(subject.strip())
    if match is None:
        return None
    return (
        match.group("workflow_id"),
        int(match.group("step_number")),
        match.group("action").strip(),
    )


@cache
def _stage_0_tool_metadata(tool_id: str) -> dict | None:
    with _STAGE_0_CONTRACT.open() as handle:
        data = json.load(handle)
    for tool in data["initial_tools"]:
        if tool["tool_id"] == tool_id:
            return tool
    return None
