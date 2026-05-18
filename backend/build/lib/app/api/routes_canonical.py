from fastapi import APIRouter, Depends, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.tool_spec import ToolSpec
from app.schemas.workflow_spec import WorkflowSpec
from app.services.canonical_compiler import (
    CanonicalPublicationError,
    ToolSpecCompiler,
    WorkflowSpecCompiler,
)
from app.services.claim_store import ClaimStore, get_claim_store


router = APIRouter(prefix="/canonical", tags=["canonical"])


@router.post("/tools/{tool_id}/publish", response_model=ToolSpec, responses=ERROR_RESPONSES)
def publish_tool_spec(
    tool_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> ToolSpec:
    try:
        compiled = ToolSpecCompiler(store).compile(tool_id)
    except CanonicalPublicationError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.CANONICAL_PUBLICATION_FAILED,
            message=str(exc),
            details={"tool_id": tool_id},
        )
    return store.save_canonical_tool_spec(compiled.spec)


@router.get("/tools/{tool_id}", response_model=ToolSpec, responses=ERROR_RESPONSES)
def get_tool_spec(
    tool_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> ToolSpec:
    spec = store.get_canonical_tool_spec(tool_id)
    if spec is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CANONICAL_SPEC_NOT_FOUND,
            message=f"Canonical ToolSpec '{tool_id}' does not exist.",
            details={"tool_id": tool_id},
        )
    return spec


@router.get("/tools", response_model=list[ToolSpec], responses=ERROR_RESPONSES)
def list_tool_specs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> list[ToolSpec]:
    return store.list_canonical_tool_specs(limit=limit, offset=offset)


@router.post(
    "/workflows/{workflow_id}/publish",
    response_model=WorkflowSpec,
    responses=ERROR_RESPONSES,
)
def publish_workflow_spec(
    workflow_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> WorkflowSpec:
    try:
        compiled = WorkflowSpecCompiler(store).compile(workflow_id)
    except CanonicalPublicationError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.CANONICAL_PUBLICATION_FAILED,
            message=str(exc),
            details={"workflow_id": workflow_id},
        )
    return store.save_canonical_workflow_spec(compiled.spec)


@router.get("/workflows/{workflow_id}", response_model=WorkflowSpec, responses=ERROR_RESPONSES)
def get_workflow_spec(
    workflow_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> WorkflowSpec:
    spec = store.get_canonical_workflow_spec(workflow_id)
    if spec is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.CANONICAL_SPEC_NOT_FOUND,
            message=f"Canonical WorkflowSpec '{workflow_id}' does not exist.",
            details={"workflow_id": workflow_id},
        )
    return spec


@router.get("/workflows", response_model=list[WorkflowSpec], responses=ERROR_RESPONSES)
def list_workflow_specs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> list[WorkflowSpec]:
    return store.list_canonical_workflow_specs(limit=limit, offset=offset)
