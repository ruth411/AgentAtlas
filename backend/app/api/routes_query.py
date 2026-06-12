"""Stage 9: Agent Query Surface — HTTP routes.

Thin layer over `QueryEngine`. Every endpoint:

- Uses Pydantic request models with `extra="forbid"` for boundary validation
- Returns the engine's typed response model verbatim
- Maps domain "not found" cases to a structured 404 with `CANONICAL_SPEC_NOT_FOUND`
- Leaves Pydantic field-validation errors to FastAPI's `RequestValidationError`
  handler, which produces the project-standard structured 422 envelope
"""

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.query import (
    AskRequest,
    AskResponse,
    GetCapabilitiesRequest,
    GetCapabilitiesResponse,
    GetConstraintsRequest,
    GetEffectsRequest,
    GetWorkflowPlanRequest,
    ExplainRiskRequest,
    ExplainRiskResponse,
    ConstraintSetResponse,
    EffectProfileResponse,
    ResolveActionRequest,
    ResolveActionResponse,
    ResolveSubjectRequest,
    ResolveSubjectResponse,
    SafeWorkflowRequest,
    SafeWorkflowResponse,
    SearchToolsResponse,
    SubjectSpecResponse,
    ValidateCommandRequest,
    ValidateCommandResponse,
    WorkflowPlanResponse,
)
from app.schemas.tool_spec import ToolSpec
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.query_engine import QueryEngine


router = APIRouter(prefix="/query", tags=["query"])


def get_query_engine(
    store: ClaimStore = Depends(get_claim_store),
) -> QueryEngine:
    """Per-request engine factory. The engine is stateless beyond the store,
    so spawning a fresh one per call is the cheapest correct pattern."""
    return QueryEngine(store)


@router.post(
    "/validate-command",
    response_model=ValidateCommandResponse,
    responses=ERROR_RESPONSES,
)
def validate_command(
    request: ValidateCommandRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> ValidateCommandResponse:
    return engine.validate_command(
        tool_id=request.tool_id, command=request.command
    )


@router.post(
    "/ask",
    response_model=AskResponse,
    responses=ERROR_RESPONSES,
)
def ask(
    request: AskRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> AskResponse:
    """Stage 17 — the headline v0.2 endpoint.

    Natural-language question → ranked, cited answers from the verified
    knowledge graph. Returns the same response shape on hit OR miss
    (`fallback_recommended=True` signals the agent should escalate to
    web_search). Reads only — no auth required even when
    `AYIRU_API_KEY` is set.
    """
    return engine.ask(
        question=request.question,
        limit=request.limit,
        tool_id_hint=request.tool_id_hint,
    )


@router.post(
    "/resolve-subject",
    response_model=ResolveSubjectResponse,
    responses=ERROR_RESPONSES,
)
def resolve_subject(
    request: ResolveSubjectRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> ResolveSubjectResponse:
    return engine.resolve_subject(
        subject_hint=request.subject_hint,
        kind=request.kind,
        family_hint=request.family_hint,
        limit=request.limit,
    )


@router.get(
    "/subjects/{subject_id}",
    response_model=SubjectSpecResponse,
    responses=ERROR_RESPONSES,
)
def get_subject_spec(
    subject_id: str = Path(..., pattern=r"^[A-Za-z0-9_.-]{1,128}$"),
    engine: QueryEngine = Depends(get_query_engine),
) -> SubjectSpecResponse:
    spec = engine.get_subject_spec(subject_id=subject_id)
    if spec is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CANONICAL_SPEC_NOT_FOUND,
            message=f"No canonical SubjectSpec published for subject_id '{subject_id}'.",
            details={"subject_id": subject_id},
        )
    return spec


@router.post(
    "/capabilities",
    response_model=GetCapabilitiesResponse,
    responses=ERROR_RESPONSES,
)
def get_capabilities(
    request: GetCapabilitiesRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> GetCapabilitiesResponse:
    return engine.get_capabilities(
        subject_id=request.subject_id,
        capability_types=list(request.capability_types),
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
        verification_min=request.verification_min,
        limit=request.limit,
    )


@router.post(
    "/constraints",
    response_model=ConstraintSetResponse,
    responses=ERROR_RESPONSES,
)
def get_constraints(
    request: GetConstraintsRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> ConstraintSetResponse:
    return engine.get_constraints(
        subject_id=request.subject_id,
        action_intent=request.action_intent,
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
    )


@router.post(
    "/effects",
    response_model=EffectProfileResponse,
    responses=ERROR_RESPONSES,
)
def get_effects(
    request: GetEffectsRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> EffectProfileResponse:
    return engine.get_effects(
        subject_id=request.subject_id,
        action_intent=request.action_intent,
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
    )


@router.post(
    "/resolve-action",
    response_model=ResolveActionResponse,
    responses=ERROR_RESPONSES,
)
def resolve_action(
    request: ResolveActionRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> ResolveActionResponse:
    return engine.resolve_action(
        subject_id=request.subject_id,
        action_intent=request.action_intent,
        command=request.command,
        environment=request.environment,
        accepted_only=request.accepted_only,
        accepted_only_structured=request.accepted_only_structured,
        limit=request.limit,
    )


@router.get(
    "/tools/{tool_id}",
    response_model=ToolSpec,
    responses=ERROR_RESPONSES,
)
def get_tool_spec(
    # Pattern matches `_validate_tool_id` in the body schemas so path-param
    # and body-supplied tool_ids reject the same garbage input the same way.
    # Without this, `GET /query/tools/bad%20id` returned 404 while
    # `POST /query/validate-command {"tool_id": "bad id"}` returned 422 —
    # same input, two different responses. Caller code can now rely on the
    # 422 envelope for malformed tool_ids regardless of route.
    tool_id: str = Path(..., pattern=r"^[A-Za-z0-9_.-]{1,128}$"),
    engine: QueryEngine = Depends(get_query_engine),
) -> ToolSpec:
    spec = engine.get_tool_spec(tool_id=tool_id)
    if spec is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CANONICAL_SPEC_NOT_FOUND,
            message=f"No canonical ToolSpec published for tool_id '{tool_id}'.",
            details={"tool_id": tool_id},
        )
    return spec


@router.get(
    "/search-tools",
    response_model=SearchToolsResponse,
    responses=ERROR_RESPONSES,
)
def search_tools(
    q: str = Query(default="", max_length=256),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    engine: QueryEngine = Depends(get_query_engine),
) -> SearchToolsResponse:
    return engine.search_tools(query=q, limit=limit, offset=offset)


@router.post(
    "/explain-risk",
    response_model=ExplainRiskResponse,
    responses=ERROR_RESPONSES,
)
def explain_risk(
    request: ExplainRiskRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> ExplainRiskResponse:
    return engine.explain_risk(
        tool_id=request.tool_id, action=request.action
    )


@router.post(
    "/safe-workflow",
    response_model=SafeWorkflowResponse,
    responses=ERROR_RESPONSES,
)
def safe_workflow(
    request: SafeWorkflowRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> SafeWorkflowResponse:
    return engine.find_safe_workflows(
        goal=request.goal, environment=request.environment
    )


@router.post(
    "/workflow-plan",
    response_model=WorkflowPlanResponse,
    responses=ERROR_RESPONSES,
)
def get_workflow_plan(
    request: GetWorkflowPlanRequest,
    engine: QueryEngine = Depends(get_query_engine),
) -> WorkflowPlanResponse:
    return engine.get_workflow_plan(
        goal=request.goal,
        environment=request.environment,
        limit=request.limit,
    )
