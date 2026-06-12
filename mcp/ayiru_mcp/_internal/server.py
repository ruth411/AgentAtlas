"""Ayiru MCP server — stdio JSON-RPC dispatcher.

One `McpServer` instance wires a `ClaimStore` (the durable backend) to a
pair of text streams (`stdin` for incoming frames, `stdout` for replies).
The main loop reads one frame per line, dispatches by method name, and
writes the response (if any) back as one line of JSON.

Public entry points:

- `McpServer(store).handle_frame(line)` — parse + dispatch + return reply
  line (or `None` for notifications). Used by in-process unit tests.
- `McpServer(store).serve(stdin, stdout)` — the blocking stdio loop the
  `python -m app.mcp_server` entry point invokes.

The dispatcher catches every exception so a buggy tool can't corrupt the
JSON-RPC stream. Application-level failures (tool raised, claim invalid,
etc.) are returned as MCP `isError: True` content blocks; protocol-level
failures (bad JSON, unknown method) are returned as JSON-RPC errors.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from typing import Any, TextIO

from ayiru_mcp._internal import protocol as proto
from ayiru_mcp._internal.tools import find_tool, list_tools
from app.services.claim_store import ClaimStore, get_claim_store


class McpServer:
    def __init__(self, store: ClaimStore, *, shared_secret: str | None = None) -> None:
        self._store = store
        self._shared_secret = shared_secret
        self._authenticated = shared_secret is None

    # -------- Public API --------

    def handle_frame(self, line: str) -> str | None:
        """Parse one wire-line and return the response line, or `None` if
        the frame was a notification (no reply by JSON-RPC contract).

        Never raises: parse errors and unknown methods become JSON-RPC
        error responses; tool-handler exceptions become `isError` content
        blocks inside a successful JSON-RPC response."""
        try:
            frame = proto.parse_incoming(line)
        except ValueError as exc:
            # We don't know the request id (the frame was unparseable);
            # JSON-RPC says use null in that case, but our model requires
            # int|str. Use 0 as a sentinel — clients receiving a parse
            # error don't typically correlate it with a specific request.
            return proto.encode_response(
                proto.make_error(0, proto.PARSE_ERROR, str(exc))
            )

        if frame is None:
            return None  # blank line — ignore

        if isinstance(frame, proto.JsonRpcNotification):
            self._dispatch_notification(frame)
            return None

        # Defensive: a buggy client may send `notifications/initialized` (or
        # any other `notifications/*` method) with a stray `id` field. The
        # MCP spec says these are notifications by method name, regardless
        # of whether an id is present. Treating them as requests and
        # replying with METHOD_NOT_FOUND would (a) break the client's
        # internal state machine if it's waiting for confirmation we
        # received the notification and (b) pollute the JSON-RPC stream.
        # Silently ack them per spec.
        if frame.method.startswith("notifications/"):
            return None

        return proto.encode_response(self._dispatch_request(frame))

    def serve(
        self,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        """Blocking stdio loop. Reads one line per frame, writes one line
        per reply. Returns when stdin closes (EOF)."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for raw_line in stdin:
            reply = self.handle_frame(raw_line)
            if reply is not None:
                stdout.write(reply + "\n")
                stdout.flush()

    # -------- Method dispatch --------

    def _dispatch_request(
        self, request: proto.JsonRpcRequest
    ) -> proto.JsonRpcResponse:
        if request.method != "initialize" and not self._authenticated:
            return proto.make_error(
                request.id,
                proto.AUTHENTICATION_REQUIRED,
                "This MCP session requires a successful initialize request with "
                "params.ayiru_shared_secret before calling other methods.",
                data={"param": "ayiru_shared_secret"},
            )
        if request.method == "initialize":
            return self._handle_initialize(request)
        if request.method == "tools/list":
            return self._handle_tools_list(request)
        if request.method == "tools/call":
            return self._handle_tools_call(request)
        if request.method in ("resources/list", "prompts/list"):
            # We don't expose resources or prompts; return an empty list
            # rather than method-not-found so clients that probe them get
            # a clean answer.
            key = request.method.split("/")[0]
            return proto.make_result(request.id, {key: []})
        return proto.make_error(
            request.id,
            proto.METHOD_NOT_FOUND,
            f"Method '{request.method}' is not supported by this MCP server.",
        )

    def _dispatch_notification(self, frame: proto.JsonRpcNotification) -> None:
        # `notifications/initialized` is the client telling us it's ready
        # to send requests. We just acknowledge by doing nothing — JSON-
        # RPC says notifications get no reply. Unknown notifications are
        # also silently ignored (per the spec).
        return

    # -------- Method handlers --------

    def _handle_initialize(
        self, request: proto.JsonRpcRequest
    ) -> proto.JsonRpcResponse:
        if self._shared_secret is not None:
            provided = request.params.get("ayiru_shared_secret")
            if not isinstance(provided, str) or not hmac.compare_digest(
                provided, self._shared_secret
            ):
                return proto.make_error(
                    request.id,
                    proto.AUTHENTICATION_REQUIRED,
                    "initialize requires params.ayiru_shared_secret matching the "
                    "server's MCP shared secret.",
                    data={"param": "ayiru_shared_secret"},
                )
            self._authenticated = True
        return proto.make_result(
            request.id,
            {
                "protocolVersion": proto.PROTOCOL_VERSION,
                "capabilities": {
                    # Tools are the only MCP capability we offer for v1.
                    # No `resources`, no `prompts`, no `logging`.
                    "tools": {},
                },
                "serverInfo": {
                    "name": proto.SERVER_NAME,
                    "version": proto.SERVER_VERSION,
                },
            },
        )

    def _handle_tools_list(
        self, request: proto.JsonRpcRequest
    ) -> proto.JsonRpcResponse:
        tools_payload = []
        for tool in list_tools():
            entry: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            # MCP 2025-06-18 ToolAnnotations — propagate iff the tool
            # declared them. Without this, Claude Desktop (and other
            # 2025-protocol hosts) fall back to name-prefix heuristics
            # that gate our `ask` / `validate_command` / `submit_claim`
            # tools out of the LLM's view. The 2026-05-22 dogfood log
            # captures the symptom.
            if tool.annotations is not None:
                entry["annotations"] = tool.annotations
            tools_payload.append(entry)
        return proto.make_result(request.id, {"tools": tools_payload})

    def _handle_tools_call(
        self, request: proto.JsonRpcRequest
    ) -> proto.JsonRpcResponse:
        name = request.params.get("name")
        # Pull `arguments` explicitly so we can distinguish "missing or null"
        # (which the spec says means empty object) from "present but not a
        # JSON object" (which is a protocol-level error). An earlier version
        # used `params.get("arguments") or {}`, which silently coerced
        # falsy non-dicts like `[]` to `{}` and let the tool handler crash
        # downstream instead of returning a clean INVALID_PARAMS.
        if "arguments" not in request.params or request.params["arguments"] is None:
            arguments: dict[str, Any] = {}
        else:
            arguments = request.params["arguments"]
        if not isinstance(name, str) or not name:
            return proto.make_error(
                request.id,
                proto.INVALID_PARAMS,
                "tools/call requires a string 'name' parameter.",
            )
        if not isinstance(arguments, dict):
            return proto.make_error(
                request.id,
                proto.INVALID_PARAMS,
                "tools/call 'arguments' must be a JSON object.",
            )
        tool = find_tool(name)
        if tool is None:
            return proto.make_error(
                request.id,
                proto.TOOL_NOT_FOUND,
                f"Unknown tool '{name}'.",
                data={"name": name},
            )
        try:
            payload = tool.handler(arguments, self._store)
        except Exception as exc:
            # Tool execution failures are surfaced as `isError: True`
            # content blocks rather than JSON-RPC errors, so MCP clients
            # see them as "tool ran but reported an error" instead of
            # "the server is broken." That matches MCP's documented model.
            return proto.make_result(
                request.id,
                _error_content(f"{type(exc).__name__}: {exc}"),
            )
        # Defensive: every registered tool returns a dict (`McpTool`'s
        # type hint says so) but a future tool could regress to returning
        # a list / scalar / None. MCP's `structuredContent` MUST be an
        # object — a non-dict payload would corrupt the response shape
        # and break MCP clients that read `structuredContent` directly.
        # Catch this here so a typo in a tool handler can't silently
        # produce malformed MCP responses.
        if not isinstance(payload, dict):
            return proto.make_result(
                request.id,
                _error_content(
                    "Tool handler returned a non-object payload "
                    f"({type(payload).__name__}); MCP requires structuredContent "
                    "to be a JSON object. This is an Ayiru bug, not a "
                    "client error — please file an issue."
                ),
            )
        return proto.make_result(
            request.id,
            _success_content(payload),
        )


# -------- Content envelope helpers --------


def _success_content(payload: dict[str, Any]) -> dict[str, Any]:
    """MCP tool results are returned as a list of content blocks. For
    structured data we JSON-encode the payload into a single text block;
    clients deserialize it back. Adding `structuredContent` so newer MCP
    clients that natively understand structured results can skip the
    string parse — older clients still get the text block."""
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
        ],
        "structuredContent": payload,
        "isError": False,
    }


def _error_content(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def build_default_server() -> McpServer:
    """Factory for the production entry point: pulls the ClaimStore
    dependency the same way every other route layer does."""
    return McpServer(
        get_claim_store(),
        shared_secret=configured_mcp_shared_secret(),
    )


def configured_mcp_shared_secret() -> str | None:
    value = os.environ.get("AYIRU_MCP_SHARED_SECRET", "").strip()
    return value or None


__all__ = [
    "McpServer",
    "build_default_server",
    "configured_mcp_shared_secret",
]
