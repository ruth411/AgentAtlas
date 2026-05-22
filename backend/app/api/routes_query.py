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
    ExplainRiskRequest,
    ExplainRiskResponse,
    SafeWorkflowRequest,
    SafeWorkflowResponse,
    SearchToolsResponse,
    ValidateCommandRequest,
    ValidateCommandResponse,
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
