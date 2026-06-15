"""Pure projections from canonical specs / risk assessments into query
response DTOs.

Extracted from `query_engine.py` to start carving the 2k-line query module
into focused, individually-testable pieces. These functions have no
dependency on the QueryEngine instance or the ranking constants — they are
straight record -> response transforms — so they live on their own and are
imported back into `query_engine` for use.
"""

from __future__ import annotations

from app.schemas.query import (
    RiskDimensions,
    SubjectSpecResponse,
    SubjectSummary,
)
from app.schemas.risk import RiskAssessment, RiskDimension
from app.schemas.tool_spec import ToolSpec
from app.schemas.workflow_spec import WorkflowSpec


def _subject_kind_for_tool_spec(spec: ToolSpec) -> str:
    interfaces = {item.lower() for item in spec.interfaces}
    if any(item in interfaces for item in {"rest", "openapi", "graphql", "api"}):
        return "api"
    if any("sdk" in item for item in interfaces):
        return "sdk"
    if "adk" in spec.tool_id.lower() or "adk" in spec.name.lower():
        return "adk"
    return "tool"


def _tool_spec_to_subject_summary(spec: ToolSpec, *, match_reason: str) -> SubjectSummary:
    family = spec.tool_id.split("-", 1)[0] if "-" in spec.tool_id else spec.tool_id
    return SubjectSummary(
        subject_id=spec.tool_id,
        subject_kind=_subject_kind_for_tool_spec(spec),  # type: ignore[arg-type]
        name=spec.name,
        family=family,
        interfaces=list(spec.interfaces),
        capability_count=len(spec.capabilities),
        verification_level=spec.verification_level,
        match_reason=match_reason,
    )


def _workflow_spec_to_subject_summary(
    spec: WorkflowSpec, *, match_reason: str
) -> SubjectSummary:
    return SubjectSummary(
        subject_id=spec.workflow_id,
        subject_kind="workflow",
        name=spec.goal or spec.workflow_id,
        family="workflow",
        interfaces=["workflow"],
        capability_count=len(spec.steps),
        verification_level=spec.verification_level,
        match_reason=match_reason or "workflow goal match",
    )


def _tool_spec_to_subject_spec(spec: ToolSpec) -> SubjectSpecResponse:
    family = spec.tool_id.split("-", 1)[0] if "-" in spec.tool_id else spec.tool_id
    return SubjectSpecResponse(
        subject_id=spec.tool_id,
        subject_kind=_subject_kind_for_tool_spec(spec),  # type: ignore[arg-type]
        name=spec.name,
        family=family,
        interfaces=list(spec.interfaces),
        capabilities=list(spec.capabilities),
        workflows=list(spec.workflows),
        verification_level=spec.verification_level,
        provenance_claim_ids=list(spec.provenance.source_claim_ids),
        provenance_evidence_ids=list(spec.provenance.source_evidence_ids),
    )


def _workflow_spec_to_subject_spec(spec: WorkflowSpec) -> SubjectSpecResponse:
    return SubjectSpecResponse(
        subject_id=spec.workflow_id,
        subject_kind="workflow",
        name=spec.goal or spec.workflow_id,
        family=spec.workflow_id,
        interfaces=[],
        capabilities=[step.action for step in spec.steps],
        workflows=[spec.workflow_id],
        verification_level=spec.verification_level,
        provenance_claim_ids=list(spec.provenance.source_claim_ids),
        provenance_evidence_ids=list(spec.provenance.source_evidence_ids),
    )


def _dimensions_from_assessment(assessment: RiskAssessment) -> RiskDimensions:
    """Project the risk engine's `RiskDimension` enum set into the boolean
    dimension flags the explain_risk response exposes. The mapping is
    intentionally direct (one dimension → one boolean) so a future
    additions to `RiskDimension` surface as a missed mapping rather than a
    silent default."""
    dims = set(assessment.dimensions)
    destructive = RiskDimension.DESTRUCTIVE in dims
    remote_mutation = RiskDimension.REMOTE_MUTATION in dims
    return RiskDimensions(
        destructive_action=destructive,
        mutates_remote_state=remote_mutation or destructive,
        reversible=not destructive,
        requires_auth=RiskDimension.AUTH_SENSITIVE in dims,
        may_cost_money=RiskDimension.COST_INCURRING in dims,
        may_expose_secrets=RiskDimension.SECRET_EXPOSURE in dims,
    )
