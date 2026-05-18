from fastapi import APIRouter, Depends, Query, status

from app.api.errors import ERROR_RESPONSES, ErrorCode, raise_api_error
from app.schemas.ingestion import (
    CliIngestionRequest,
    CliIngestionResponse,
    DocsIngestionRequest,
    DocsIngestionResponse,
    GraphqlIngestionRequest,
    GraphqlIngestionResponse,
    IngestionRun,
    JsonSchemaIngestionRequest,
    JsonSchemaIngestionResponse,
    McpIngestionRequest,
    McpIngestionResponse,
    OpenApiIngestionRequest,
    OpenApiIngestionResponse,
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
from app.services.graphql_ingestion import (
    GraphqlHttpClient,
    GraphqlIngestionError,
    GraphqlIngestionService,
    HttpxGraphqlClient,
)
from app.services.json_schema_ingestion import (
    HttpxJsonSchemaClient,
    JsonSchemaHttpClient,
    JsonSchemaIngestionError,
    JsonSchemaIngestionService,
)
from app.services.mcp_ingestion import (
    McpIngestionError,
    McpIngestionService,
    McpServerRunner,
    SafeMcpServerRunner,
)
from app.services.openapi_ingestion import (
    HttpxOpenApiClient,
    OpenApiHttpClient,
    OpenApiIngestionError,
    OpenApiIngestionService,
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


# -------- Stage 7c.1: OpenAPI Schema Ingestion --------


def get_openapi_client() -> OpenApiHttpClient:
    return HttpxOpenApiClient()


@router.post(
    "/openapi", response_model=OpenApiIngestionResponse, responses=ERROR_RESPONSES
)
def ingest_openapi(
    request: OpenApiIngestionRequest,
    store: ClaimStore = Depends(get_claim_store),
    client: OpenApiHttpClient = Depends(get_openapi_client),
) -> OpenApiIngestionResponse:
    try:
        return OpenApiIngestionService(store, client=client).ingest(
            tool_id=request.tool_id,
            url=request.url,
            submitted_by=request.submitted_by,
            verify=request.verify,
        )
    except OpenApiIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.OPENAPI_INGESTION_FAILED,
            message=str(exc),
            details={"tool_id": request.tool_id, "url": request.url},
        )


@router.post(
    "/openapi/tools/{tool_id}",
    response_model=list[OpenApiIngestionResponse],
    responses=ERROR_RESPONSES,
)
def ingest_all_openapi_for_tool(
    tool_id: str,
    submitted_by: str = "openapi-ingestion-agent",
    verify: bool = True,
    store: ClaimStore = Depends(get_claim_store),
    client: OpenApiHttpClient = Depends(get_openapi_client),
) -> list[OpenApiIngestionResponse]:
    try:
        return OpenApiIngestionService(store, client=client).ingest_all_for_tool(
            tool_id=tool_id,
            submitted_by=submitted_by,
            verify=verify,
        )
    except OpenApiIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.OPENAPI_INGESTION_FAILED,
            message=str(exc),
            details={"tool_id": tool_id},
        )


# -------- Stage 7c.2: JSON Schema Ingestion --------


def get_json_schema_client() -> JsonSchemaHttpClient:
    return HttpxJsonSchemaClient()


@router.post(
    "/json_schema",
    response_model=JsonSchemaIngestionResponse,
    responses=ERROR_RESPONSES,
)
def ingest_json_schema(
    request: JsonSchemaIngestionRequest,
    store: ClaimStore = Depends(get_claim_store),
    client: JsonSchemaHttpClient = Depends(get_json_schema_client),
) -> JsonSchemaIngestionResponse:
    try:
        return JsonSchemaIngestionService(store, client=client).ingest(
            tool_id=request.tool_id,
            url=request.url,
            submitted_by=request.submitted_by,
            verify=request.verify,
        )
    except JsonSchemaIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.JSON_SCHEMA_INGESTION_FAILED,
            message=str(exc),
            details={"tool_id": request.tool_id, "url": request.url},
        )


@router.post(
    "/json_schema/tools/{tool_id}",
    response_model=list[JsonSchemaIngestionResponse],
    responses=ERROR_RESPONSES,
)
def ingest_all_json_schema_for_tool(
    tool_id: str,
    submitted_by: str = "json-schema-ingestion-agent",
    verify: bool = True,
    store: ClaimStore = Depends(get_claim_store),
    client: JsonSchemaHttpClient = Depends(get_json_schema_client),
) -> list[JsonSchemaIngestionResponse]:
    try:
        return JsonSchemaIngestionService(store, client=client).ingest_all_for_tool(
            tool_id=tool_id,
            submitted_by=submitted_by,
            verify=verify,
        )
    except JsonSchemaIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.JSON_SCHEMA_INGESTION_FAILED,
            message=str(exc),
            details={"tool_id": tool_id},
        )


# -------- Stage 7c.3: GraphQL SDL Ingestion --------


def get_graphql_client() -> GraphqlHttpClient:
    return HttpxGraphqlClient()


@router.post(
    "/graphql",
    response_model=GraphqlIngestionResponse,
    responses=ERROR_RESPONSES,
)
def ingest_graphql(
    request: GraphqlIngestionRequest,
    store: ClaimStore = Depends(get_claim_store),
    client: GraphqlHttpClient = Depends(get_graphql_client),
) -> GraphqlIngestionResponse:
    try:
        return GraphqlIngestionService(store, client=client).ingest(
            tool_id=request.tool_id,
            url=request.url,
            submitted_by=request.submitted_by,
            verify=request.verify,
        )
    except GraphqlIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.GRAPHQL_INGESTION_FAILED,
            message=str(exc),
            details={"tool_id": request.tool_id, "url": request.url},
        )


@router.post(
    "/graphql/tools/{tool_id}",
    response_model=list[GraphqlIngestionResponse],
    responses=ERROR_RESPONSES,
)
def ingest_all_graphql_for_tool(
    tool_id: str,
    submitted_by: str = "graphql-ingestion-agent",
    verify: bool = True,
    store: ClaimStore = Depends(get_claim_store),
    client: GraphqlHttpClient = Depends(get_graphql_client),
) -> list[GraphqlIngestionResponse]:
    try:
        return GraphqlIngestionService(store, client=client).ingest_all_for_tool(
            tool_id=tool_id,
            submitted_by=submitted_by,
            verify=verify,
        )
    except GraphqlIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.GRAPHQL_INGESTION_FAILED,
            message=str(exc),
            details={"tool_id": tool_id},
        )


# -------- Stage 7d: MCP Server Metadata Ingestion --------


def get_mcp_runner() -> McpServerRunner:
    return SafeMcpServerRunner()


@router.post(
    "/mcp",
    response_model=McpIngestionResponse,
    responses=ERROR_RESPONSES,
)
def ingest_mcp(
    request: McpIngestionRequest,
    store: ClaimStore = Depends(get_claim_store),
    runner: McpServerRunner = Depends(get_mcp_runner),
) -> McpIngestionResponse:
    try:
        return McpIngestionService(store, runner=runner).ingest(
            server_id=request.server_id,
            submitted_by=request.submitted_by,
            verify=request.verify,
        )
    except McpIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.MCP_INGESTION_FAILED,
            message=str(exc),
            details={"server_id": request.server_id},
        )


@router.post(
    "/mcp/publishers/{publisher}",
    response_model=list[McpIngestionResponse],
    responses=ERROR_RESPONSES,
)
def ingest_all_mcp_for_publisher(
    publisher: str,
    submitted_by: str = "mcp-ingestion-agent",
    verify: bool = True,
    store: ClaimStore = Depends(get_claim_store),
    runner: McpServerRunner = Depends(get_mcp_runner),
) -> list[McpIngestionResponse]:
    try:
        return McpIngestionService(store, runner=runner).ingest_all_for_publisher(
            publisher=publisher,
            submitted_by=submitted_by,
            verify=verify,
        )
    except McpIngestionError as exc:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.MCP_INGESTION_FAILED,
            message=str(exc),
            details={"publisher": publisher},
        )
