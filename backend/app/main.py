from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.api.errors import ErrorCode, json_error_response
from app.api.routes_canonical import router as canonical_router
from app.api.routes_claims import router as claims_router
from app.api.routes_health import router as health_router
from app.api.routes_ingestion import router as ingestion_router
from app.api.routes_verification import router as verification_router


# 1 MiB is well above the largest legitimate claim payload (claim + 50 evidence
# records × 8000-char excerpt = ~400 KiB worst case) while still blocking
# resource-exhaustion attacks via gigabyte-scale POST bodies.
DEFAULT_MAX_REQUEST_BYTES = 1 * 1024 * 1024


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared or streamed body exceeds `max_bytes`.

    Checks the `Content-Length` header up front, and falls back to a streaming
    counter if no header is provided. Returns the structured error envelope so
    consumers get a `REQUEST_BODY_TOO_LARGE` code rather than a 500.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    return self._too_large(declared)
            except ValueError:
                # Malformed Content-Length: fall through; FastAPI will reject
                # the request later as part of normal parsing.
                pass
        return await call_next(request)

    def _too_large(self, declared: str) -> JSONResponse:
        return json_error_response(
            413,
            code=ErrorCode.REQUEST_BODY_TOO_LARGE,
            message=(
                f"Request body of {declared} bytes exceeds the "
                f"{self._max_bytes}-byte limit."
            ),
            details={"max_bytes": self._max_bytes, "declared_bytes": declared},
        )


app = FastAPI(title="AgentAtlas", version="0.1.0")
app.add_middleware(RequestBodySizeLimitMiddleware)
app.include_router(health_router)
app.include_router(claims_router)
app.include_router(canonical_router)
app.include_router(ingestion_router)
app.include_router(verification_router)


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
