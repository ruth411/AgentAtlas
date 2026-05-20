from enum import StrEnum
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    CANONICAL_PUBLICATION_FAILED = "CANONICAL_PUBLICATION_FAILED"
    CANONICAL_SPEC_NOT_FOUND = "CANONICAL_SPEC_NOT_FOUND"
    AUDIT_EVENT_QUERY_INVALID = "AUDIT_EVENT_QUERY_INVALID"
    CLAIM_ALREADY_EXISTS = "CLAIM_ALREADY_EXISTS"
    CLAIM_NOT_FOUND = "CLAIM_NOT_FOUND"
    HUMAN_REVIEW_FAILED = "HUMAN_REVIEW_FAILED"
    HUMAN_REVIEW_ALREADY_EXISTS = "HUMAN_REVIEW_ALREADY_EXISTS"
    DOCS_FETCH_FAILED = "DOCS_FETCH_FAILED"
    EVIDENCE_ALREADY_EXISTS = "EVIDENCE_ALREADY_EXISTS"
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    HTTP_ERROR = "HTTP_ERROR"
    INGESTION_ARTIFACT_NOT_FOUND = "INGESTION_ARTIFACT_NOT_FOUND"
    INGESTION_RUN_NOT_FOUND = "INGESTION_RUN_NOT_FOUND"
    INVALID_CLAIM_SCHEMA = "INVALID_CLAIM_SCHEMA"
    INVALID_INGESTION_REQUEST = "INVALID_INGESTION_REQUEST"
    GRAPHQL_INGESTION_FAILED = "GRAPHQL_INGESTION_FAILED"
    INVALID_QUERY_PARAMETER = "INVALID_QUERY_PARAMETER"
    JSON_SCHEMA_INGESTION_FAILED = "JSON_SCHEMA_INGESTION_FAILED"
    MCP_INGESTION_FAILED = "MCP_INGESTION_FAILED"
    OPENAPI_INGESTION_FAILED = "OPENAPI_INGESTION_FAILED"
    REJECTED_PRIMARY_EVIDENCE = "REJECTED_PRIMARY_EVIDENCE"
    REQUEST_BODY_TOO_LARGE = "REQUEST_BODY_TOO_LARGE"
    RUNTIME_VERIFICATION_FAILED = "RUNTIME_VERIFICATION_FAILED"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    VERIFICATION_RESULT_NOT_FOUND = "VERIFICATION_RESULT_NOT_FOUND"


class ApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ApiError


ERROR_RESPONSES = {
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
    413: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
}


def is_structured_error_detail(detail: object) -> bool:
    return isinstance(detail, dict) and {"code", "message", "details"}.issubset(detail)


def error_payload(
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }


def raise_api_error(
    status_code: int,
    *,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=error_payload(code=code, message=message, details=details)["error"],
    )


def json_error_response(
    status_code: int,
    *,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code=code, message=message, details=details),
    )
