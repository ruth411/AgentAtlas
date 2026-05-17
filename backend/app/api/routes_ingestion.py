from fastapi import APIRouter, Depends, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.ingestion import (
    CliIngestionRequest,
    CliIngestionResponse,
    DocsIngestionRequest,
    DocsIngestionResponse,
    IngestionRun,
    RawIngestionArtifact,
)
from app.services.claim_store import ClaimStore, get_claim_store
from app.services.cli_ingestion import (
    CliIngestionError,
    CliIngestionService,
    CliRunner,
    SafeCliRunner,
)
from app.services.docs_ingestion import (
    DocsHttpClient,
    DocsIngestionError,
    DocsIngestionService,
    HttpxDocsClient,
)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def get_cli_runner() -> CliRunner:
    return SafeCliRunner()


@router.post("/cli", response_model=CliIngestionResponse, responses=ERROR_RESPONSES)
def ingest_cli(
    request: CliIngestionRequest,
    store: ClaimStore = Depends(get_claim_store),
    runner: CliRunner = Depends(get_cli_runner),
) -> CliIngestionResponse:
    try:
        return CliIngestionService(store, runner=runner).ingest(
            tool_id=request.tool_id,
            command=request.command,
            submitted_by=request.submitted_by,
            verify=request.verify,
        )
    except CliIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.INVALID_INGESTION_REQUEST,
            message=str(exc),
            details={"tool_id": request.tool_id, "command": request.command},
        )


@router.post(
    "/cli/tools/{tool_id}",
    response_model=list[CliIngestionResponse],
    responses=ERROR_RESPONSES,
)
def ingest_all_for_tool(
    tool_id: str,
    submitted_by: str = "cli-ingestion-agent",
    verify: bool = True,
    store: ClaimStore = Depends(get_claim_store),
    runner: CliRunner = Depends(get_cli_runner),
) -> list[CliIngestionResponse]:
    try:
        return CliIngestionService(store, runner=runner).ingest_all_for_tool(
            tool_id=tool_id,
            submitted_by=submitted_by,
            verify=verify,
        )
    except CliIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.INVALID_INGESTION_REQUEST,
            message=str(exc),
            details={"tool_id": tool_id},
        )


@router.get("/runs", response_model=list[IngestionRun], responses=ERROR_RESPONSES)
def list_ingestion_runs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ClaimStore = Depends(get_claim_store),
) -> list[IngestionRun]:
    return store.list_ingestion_runs(limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=IngestionRun, responses=ERROR_RESPONSES)
def get_ingestion_run(
    run_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> IngestionRun:
    run = store.get_ingestion_run(run_id)
    if run is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.INGESTION_RUN_NOT_FOUND,
            message=f"Ingestion run '{run_id}' does not exist.",
            details={"run_id": run_id},
        )
    return run


@router.get(
    "/artifacts/{artifact_id}",
    response_model=RawIngestionArtifact,
    responses=ERROR_RESPONSES,
)
def get_ingestion_artifact(
    artifact_id: str,
    store: ClaimStore = Depends(get_claim_store),
) -> RawIngestionArtifact:
    artifact = store.get_ingestion_artifact(artifact_id)
    if artifact is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCode.INGESTION_ARTIFACT_NOT_FOUND,
            message=f"Ingestion artifact '{artifact_id}' does not exist.",
            details={"artifact_id": artifact_id},
        )
    return artifact


# -------- Stage 7b: Docs Ingestion --------


def get_docs_client() -> DocsHttpClient:
    return HttpxDocsClient()


@router.post("/docs", response_model=DocsIngestionResponse, responses=ERROR_RESPONSES)
def ingest_docs(
    request: DocsIngestionRequest,
    store: ClaimStore = Depends(get_claim_store),
    client: DocsHttpClient = Depends(get_docs_client),
) -> DocsIngestionResponse:
    try:
        return DocsIngestionService(store, client=client).ingest(
            tool_id=request.tool_id,
            url=request.url,
            submitted_by=request.submitted_by,
            verify=request.verify,
        )
    except DocsIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.DOCS_FETCH_FAILED,
            message=str(exc),
            details={"tool_id": request.tool_id, "url": request.url},
        )


@router.post(
    "/docs/tools/{tool_id}",
    response_model=list[DocsIngestionResponse],
    responses=ERROR_RESPONSES,
)
def ingest_all_docs_for_tool(
    tool_id: str,
    submitted_by: str = "docs-ingestion-agent",
    verify: bool = True,
    store: ClaimStore = Depends(get_claim_store),
    client: DocsHttpClient = Depends(get_docs_client),
) -> list[DocsIngestionResponse]:
    try:
        return DocsIngestionService(store, client=client).ingest_all_for_tool(
            tool_id=tool_id,
            submitted_by=submitted_by,
            verify=verify,
        )
    except DocsIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.DOCS_FETCH_FAILED,
            message=str(exc),
            details={"tool_id": tool_id},
        )
