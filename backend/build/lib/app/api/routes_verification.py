from fastapi import APIRouter, Depends, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.verification import (
    BulkRuntimeVerificationResponse,
    RuntimeVerificationRequest,
    RuntimeVerificationResponse,
)
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.runtime_verifier import (
    HttpHeadClient,
    HttpxHeadClient,
    RuntimeVerificationError,
    RuntimeVerificationService,
    SandboxRunner,
    SubprocessSandboxRunner,
)
from app.services.mcp_ingestion import McpServerRunner, SafeMcpServerRunner


router = APIRouter(prefix="/verification", tags=["verification"])


def get_sandbox_runner() -> SandboxRunner:
    return SubprocessSandboxRunner()


def get_head_client() -> HttpHeadClient:
    return HttpxHeadClient()


def get_runtime_mcp_runner() -> McpServerRunner:
    return SafeMcpServerRunner()


@router.post(
    "/runtime",
    response_model=RuntimeVerificationResponse,
    responses=ERROR_RESPONSES,
)
def verify_runtime(
    request: RuntimeVerificationRequest,
    store: ClaimStore = Depends(get_claim_store),
    sandbox: SandboxRunner = Depends(get_sandbox_runner),
    head_client: HttpHeadClient = Depends(get_head_client),
    mcp_runner: McpServerRunner = Depends(get_runtime_mcp_runner),
) -> RuntimeVerificationResponse:
    try:
        return RuntimeVerificationService(
            store,
            sandbox=sandbox,
            head_client=head_client,
            mcp_runner=mcp_runner,
        ).verify(
            claim_id=request.claim_id,
            submitted_by=request.submitted_by,
        )
    except RuntimeVerificationError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.RUNTIME_VERIFICATION_FAILED,
            message=str(exc),
            details={"claim_id": request.claim_id},
        )


@router.post(
    "/runtime/tools/{tool_id}",
    response_model=BulkRuntimeVerificationResponse,
    responses=ERROR_RESPONSES,
)
def verify_runtime_all_for_tool(
    tool_id: str,
    submitted_by: str = "runtime-verifier-agent",
    limit: int = Query(default=100, ge=1, le=500),
    store: ClaimStore = Depends(get_claim_store),
    sandbox: SandboxRunner = Depends(get_sandbox_runner),
    head_client: HttpHeadClient = Depends(get_head_client),
    mcp_runner: McpServerRunner = Depends(get_runtime_mcp_runner),
) -> BulkRuntimeVerificationResponse:
    try:
        results = RuntimeVerificationService(
            store,
            sandbox=sandbox,
            head_client=head_client,
            mcp_runner=mcp_runner,
        ).verify_all_for_tool(
            tool_id=tool_id,
            submitted_by=submitted_by,
            limit=limit,
        )
    except RuntimeVerificationError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.RUNTIME_VERIFICATION_FAILED,
            message=str(exc),
            details={"tool_id": tool_id},
        )
    promoted = sum(1 for r in results if r.promoted_to_l3)
    skipped = sum(1 for r in results if r.skipped)
    failed = sum(
        1
        for r in results
        if (not r.skipped) and (not r.runtime_check_passed)
    )
    return BulkRuntimeVerificationResponse(
        tool_id=tool_id,
        attempted=len(results),
        promoted=promoted,
        skipped=skipped,
        failed=failed,
        results=results,
    )
