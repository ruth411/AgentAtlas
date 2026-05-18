"""JSON-RPC 2.0 framing for the AgentAtlas MCP server.

Thin wrapper. Every message is a single line of JSON. We hand-roll this
because the MCP protocol surface we need (`initialize`, `tools/list`,
`tools/call`, and the `notifications/initialized` ack) is small and stable;
pulling in `pydantic` here keeps validation honest without growing the
dependency footprint.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# JSON-RPC error codes (standard + a few MCP-application codes).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application-specific (MCP layer). Reserved range: -32000 to -32099.
TOOL_NOT_FOUND = -32001
TOOL_EXECUTION_ERROR = -32002


# Protocol version we advertise on initialize. Matches the MCP spec
# revision Claude Desktop / Cursor ship as of this release.
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "AgentAtlas"
SERVER_VERSION = "0.1.0"


# -------- Wire-level frames --------


class JsonRpcRequest(BaseModel):
    """A request: must have `id` (notification has no id and uses a
    different model). We're permissive about extra fields so a future MCP
    revision adding optional keys doesn't crash older servers."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    jsonrpc: Literal["2.0"]
    id: int | str
    method: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcNotification(BaseModel):
    """A notification: no `id` field by definition. Server MUST NOT reply."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    jsonrpc: Literal["2.0"]
    method: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcError(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: int
    message: str = Field(min_length=1, max_length=1024)
    data: dict[str, Any] | None = None


class JsonRpcResponse(BaseModel):
    """One of `result` or `error` is set; never both."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str
    result: dict[str, Any] | None = None
    error: JsonRpcError | None = None


# -------- Parsing helpers --------


def parse_incoming(line: str) -> JsonRpcRequest | JsonRpcNotification | None:
    """Parse a single line. Returns None on blank input; raises ValueError
    on un-parseable input so the caller can produce a JSON-RPC error reply.

    A frame is a notification iff it lacks an `id` field — that's how
    JSON-RPC 2.0 distinguishes them. We dispatch on that here so the
    server loop doesn't have to."""
    text = line.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("frame must be a JSON object")
    if parsed.get("jsonrpc") != "2.0":
        raise ValueError("frame must use jsonrpc=2.0")
    if "id" in parsed:
        return JsonRpcRequest.model_validate(parsed)
    return JsonRpcNotification.model_validate(parsed)


def encode_response(response: JsonRpcResponse) -> str:
    """Encode a response as a single line of JSON (no trailing newline —
    the caller writes the newline so framing stays explicit)."""
    return response.model_dump_json(exclude_none=True)


def make_result(request_id: int | str, result: dict[str, Any]) -> JsonRpcResponse:
    return JsonRpcResponse(id=request_id, result=result)


def make_error(
    request_id: int | str,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> JsonRpcResponse:
    return JsonRpcResponse(
        id=request_id,
        error=JsonRpcError(code=code, message=message, data=data),
    )


__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JsonRpcError",
    "JsonRpcNotification",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "TOOL_EXECUTION_ERROR",
    "TOOL_NOT_FOUND",
    "encode_response",
    "make_error",
    "make_result",
    "parse_incoming",
]
