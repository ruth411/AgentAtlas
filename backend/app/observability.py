"""Stage 14: lightweight request observability.

`RequestObservabilityMiddleware` does three things on every HTTP request:

1. **Assign a request id.** Reads `X-Request-ID` from the incoming
   request if the client sent one (useful for tracing across a reverse
   proxy); otherwise mints a fresh `uuid4`.
2. **Echo the id back.** Returns `X-Request-ID` on every response so
   clients (and the dashboard) can correlate failures with server logs.
3. **Emit one structured log line per request.** JSON shape so log
   aggregators (Datadog / Loki / CloudWatch) can index without parsing
   tracebacks. Format: ``{"event": "http_request", "request_id": ...,
   "method": "POST", "path": "/v1/claims", "status_code": 201,
   "duration_ms": 42, "client_host": "127.0.0.1"}``.

No external logging dependency. Uses the stdlib `logging` module with a
JSON-formatted log line; deployments wire their own log handler.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ayiru_request_id", default=""
)

_logger = logging.getLogger("ayiru.request")


def current_request_id() -> str:
    """Return the current request's id (empty string when not inside a
    request — e.g. CLI invocations). Useful for attaching the id to
    audit events, log lines, and error reports emitted from deeper
    layers."""
    return _request_id_var.get()


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    """Stamp a request id and emit one structured log line per request.

    Mounted as a middleware so it sees every route (legacy and `/v1/`).
    Failures inside the wrapped handler propagate normally; the log
    line is still emitted with `status_code: 500` and the exception
    type, so a crashed request is never invisible to ops.
    """

    _REQUEST_ID_HEADER = "X-Request-ID"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        incoming = request.headers.get(self._REQUEST_ID_HEADER, "").strip()
        request_id = incoming or uuid.uuid4().hex
        token = _request_id_var.set(request_id)
        started = time.perf_counter()
        client_host = request.client.host if request.client else ""

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _emit_log_line(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=500,
                duration_ms=duration_ms,
                client_host=client_host,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            _request_id_var.reset(token)

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[self._REQUEST_ID_HEADER] = request_id
        _emit_log_line(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_host=client_host,
            error_type=None,
        )
        return response


def _emit_log_line(
    *,
    request_id: str,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    client_host: str,
    error_type: str | None,
) -> None:
    payload = {
        "event": "http_request",
        "request_id": request_id,
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "client_host": client_host,
    }
    if error_type is not None:
        payload["error_type"] = error_type
    # One JSON line — easy to grep, easy to ingest by structured log
    # collectors. Errors land at WARNING so they don't drown in INFO
    # but also don't trigger alert-on-ERROR rules for ordinary 4xx.
    level = logging.WARNING if status_code >= 500 else logging.INFO
    _logger.log(level, json.dumps(payload, ensure_ascii=False))
