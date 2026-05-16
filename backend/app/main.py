from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.errors import ErrorCode, json_error_response
from app.api.routes_canonical import router as canonical_router
from app.api.routes_claims import router as claims_router
from app.api.routes_health import router as health_router

app = FastAPI(title="AgentAtlas", version="0.1.0")
app.include_router(health_router)
app.include_router(claims_router)
app.include_router(canonical_router)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(_, exc: RequestValidationError) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    location = first_error.get("loc", [])
    field = ".".join(str(part) for part in location if part != "body")
    message = first_error.get("msg", "Invalid request payload")
    code = (
        ErrorCode.INVALID_QUERY_PARAMETER
        if location and location[0] == "query"
        else ErrorCode.INVALID_CLAIM_SCHEMA
    )

    return json_error_response(
        422,
        code=code,
        message=message,
        details={"field": field or "body"},
    )


@app.exception_handler(HTTPException)
async def handle_http_exception(_, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and {"code", "message", "details"}.issubset(exc.detail):
        return json_error_response(
            exc.status_code,
            code=ErrorCode(str(exc.detail["code"])),
            message=str(exc.detail["message"]),
            details=dict(exc.detail["details"]),
        )

    return json_error_response(
        exc.status_code,
        code=ErrorCode.HTTP_ERROR,
        message=str(exc.detail),
        details={},
    )
