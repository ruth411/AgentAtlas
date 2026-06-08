import os
import threading
import time
from collections import deque
from math import ceil

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import ErrorCode, json_error_response
from app.auth import ApiKeyAuthMiddleware
from app.observability import RequestObservabilityMiddleware
from app.api.routes_audit import router as audit_router
from app.api.routes_canonical import router as canonical_router
from app.api.routes_claims import router as claims_router
from app.api.routes_health import router as health_router
from app.api.routes_human_review import router as human_review_router
from app.api.routes_ingestion import router as ingestion_router
from app.api.routes_query import router as query_router
from app.api.routes_stats import router as stats_router
from app.api.routes_verification import router as verification_router


# 1 MiB is well above the largest legitimate claim payload (claim + 50 evidence
# records × 8000-char excerpt = ~400 KiB worst case) while still blocking
# resource-exhaustion attacks via gigabyte-scale POST bodies.
DEFAULT_MAX_REQUEST_BYTES = 1 * 1024 * 1024
_ASK_RATE_LIMIT_ENV = "AYIRU_ASK_RATE_LIMIT_REQUESTS"
_ASK_RATE_LIMIT_WINDOW_ENV = "AYIRU_ASK_RATE_LIMIT_WINDOW_SECONDS"
_DEFAULT_ASK_RATE_LIMIT_WINDOW_SECONDS = 60
# Only honour `X-Forwarded-For` for rate-limit keying when the operator
# opts in (i.e. they actually run behind a trusted reverse proxy). The
# header is client-controlled, so trusting it unconditionally would let
# anyone rotate it to mint a fresh quota per request — the rate limit
# would be a no-op against a deliberate attacker.
_RATE_LIMIT_TRUST_FORWARDED_FOR_ENV = "AYIRU_RATE_LIMIT_TRUST_FORWARDED_FOR"
_TRUSTED_HOSTS_ENV = "AYIRU_TRUSTED_HOSTS"


class RequestBodySizeLimitMiddleware:
    """Reject requests whose declared or streamed body exceeds `max_bytes`.

    The middleware checks `Content-Length` up front when present, then buffers
    the incoming request stream up to the limit before handing control to
    FastAPI. That closes the `Transfer-Encoding: chunked` gap where a client
    omits Content-Length and streams arbitrarily large bodies into the parser.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> None:
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = self._header_value(scope, b"content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    await self._too_large(declared_bytes=declared)(scope, receive, send)
                    return
            except ValueError:
                # Malformed Content-Length: fall through; FastAPI will reject
                # the request later as part of normal parsing.
                pass

        buffered_messages: list[Message] = []
        streamed_bytes = 0
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] != "http.request":
                break
            streamed_bytes += len(message.get("body", b""))
            if streamed_bytes > self._max_bytes:
                await self._too_large(streamed_bytes=streamed_bytes)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replay_receive = self._replay_receive(buffered_messages)
        await self.app(scope, replay_receive, send)

    def _header_value(self, scope: Scope, name: bytes) -> str | None:
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == name:
                return header_value.decode("latin-1")
        return None

    def _replay_receive(self, buffered_messages: list[Message]) -> Receive:
        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(buffered_messages):
                message = buffered_messages[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        return replay

    def _too_large(
        self,
        *,
        declared_bytes: str | None = None,
        streamed_bytes: int | None = None,
    ) -> JSONResponse:
        details = {"max_bytes": self._max_bytes}
        if declared_bytes is not None:
            details["declared_bytes"] = declared_bytes
            message = (
                f"Request body of {declared_bytes} bytes exceeds the "
                f"{self._max_bytes}-byte limit."
            )
        else:
            details["streamed_bytes"] = streamed_bytes
            message = (
                f"Request body exceeded the {self._max_bytes}-byte limit "
                f"while streaming ({streamed_bytes} bytes read)."
            )
        return json_error_response(
            413,
            code=ErrorCode.REQUEST_BODY_TOO_LARGE,
            message=message,
            details=details,
        )


class LegacyDeprecationHeaderMiddleware(BaseHTTPMiddleware):
    """Stage 14: stamp a `Deprecation` header on un-versioned responses.

    The canonical API surface lives under `/v1/`. The un-versioned routes
    are kept for one release so existing dashboards, scripts, and the
    MCP server's in-process callers don't break overnight. Any client
    receiving a `Deprecation: true` header is reading from the legacy
    mount and should switch to the `/v1/`-prefixed equivalent before the
    next release (which removes the legacy paths).
    """

    _LEGACY_PREFIXES = (
        "/claims",
        "/canonical",
        "/ingestion",
        "/verification",
        "/query",
        "/audit",
    )
    _SUNSET_DATE = "Wed, 31 Dec 2026 00:00:00 GMT"

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._LEGACY_PREFIXES):
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = self._SUNSET_DATE
            # Per RFC 8594 — point clients at the replacement.
            response.headers["Link"] = (
                f'</v1{path}>; rel="successor-version"'
            )
        return response


class AskRateLimitMiddleware(BaseHTTPMiddleware):
    """Optionally rate-limit the public `ask()` endpoint per client.

    Disabled by default. When `AYIRU_ASK_RATE_LIMIT_REQUESTS` is set to a
    positive integer, the middleware enforces a fixed-window quota over
    `POST /query/ask` and `POST /v1/query/ask`, keyed by client IP (or the
    first `X-Forwarded-For` hop when present). This keeps the public read
    surface open while giving operators a low-friction abuse brake.
    """

    _ASK_PATHS = frozenset({"/query/ask", "/v1/query/ask"})
    # Sweep idle client buckets once the table grows past this many keys,
    # so the limiter can't be turned into a memory-exhaustion vector.
    _SWEEP_THRESHOLD = 10_000

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._lock = threading.Lock()
        self._seen_at: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        limit = self._configured_limit()
        if limit is None or request.method != "POST" or request.url.path not in self._ASK_PATHS:
            return await call_next(request)

        window_seconds = self._configured_window_seconds()
        client_id = self._client_id(request)
        now = time.monotonic()
        retry_after_seconds = self._consume_or_retry_after(
            client_id=client_id,
            now=now,
            limit=limit,
            window_seconds=window_seconds,
        )
        if retry_after_seconds is not None:
            return json_error_response(
                429,
                code=ErrorCode.RATE_LIMITED,
                message=(
                    f"Rate limit exceeded for ask(). Max {limit} request(s) per "
                    f"{window_seconds} second window."
                ),
                details={
                    "client_id": client_id,
                    "limit": limit,
                    "window_seconds": window_seconds,
                    "retry_after_seconds": retry_after_seconds,
                },
                headers={"Retry-After": str(retry_after_seconds)},
            )
        return await call_next(request)

    def _configured_limit(self) -> int | None:
        raw = os.environ.get(_ASK_RATE_LIMIT_ENV, "").strip()
        if not raw:
            return None
        try:
            limit = int(raw)
        except ValueError:
            return None
        return limit if limit > 0 else None

    def _configured_window_seconds(self) -> int:
        raw = os.environ.get(_ASK_RATE_LIMIT_WINDOW_ENV, "").strip()
        if not raw:
            return _DEFAULT_ASK_RATE_LIMIT_WINDOW_SECONDS
        try:
            window = int(raw)
        except ValueError:
            return _DEFAULT_ASK_RATE_LIMIT_WINDOW_SECONDS
        return window if window > 0 else _DEFAULT_ASK_RATE_LIMIT_WINDOW_SECONDS

    def _client_id(self, request: Request) -> str:
        if self._trust_forwarded_for():
            forwarded_for = request.headers.get("x-forwarded-for", "").strip()
            if forwarded_for:
                return forwarded_for.split(",", 1)[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    def _trust_forwarded_for(self) -> bool:
        raw = os.environ.get(_RATE_LIMIT_TRUST_FORWARDED_FOR_ENV, "").strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _consume_or_retry_after(
        self,
        *,
        client_id: str,
        now: float,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        cutoff = now - float(window_seconds)
        with self._lock:
            self._sweep_expired(cutoff)
            bucket = self._seen_at.setdefault(client_id, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, ceil(window_seconds - (now - bucket[0])))
                return retry_after
            bucket.append(now)
            return None

    def _sweep_expired(self, cutoff: float) -> None:
        """Drop fully-expired idle buckets so `_seen_at` stays bounded.

        Runs under the caller's lock and only once the table is large, so
        the cost is amortised. Without this, a client rotating its key
        (e.g. a spoofed X-Forwarded-For when trusted) would grow the dict
        without limit — turning the abuse brake into a DoS vector.
        """
        if len(self._seen_at) < self._SWEEP_THRESHOLD:
            return
        stale = [
            cid
            for cid, bucket in self._seen_at.items()
            if not bucket or bucket[-1] <= cutoff
        ]
        for cid in stale:
            self._seen_at.pop(cid, None)


class TrustedHostGuardMiddleware(BaseHTTPMiddleware):
    """Optionally reject requests whose Host header is not allowlisted.

    Disabled by default. When `AYIRU_TRUSTED_HOSTS` is set to a comma-separated
    list, requests must present a matching host (port ignored). Supports exact
    entries, `*.example.com` wildcard entries, and `*` to allow any host.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        allowed = self._configured_hosts()
        if allowed is None:
            return await call_next(request)

        host = (request.url.hostname or "").lower()
        if not host or not any(self._host_matches(host, pattern) for pattern in allowed):
            return json_error_response(
                400,
                code=ErrorCode.HTTP_ERROR,
                message=f"Host {host or '<missing>'!r} is not in AYIRU_TRUSTED_HOSTS.",
                details={"host": host or "", "allowed_hosts": sorted(allowed)},
            )
        return await call_next(request)

    def _configured_hosts(self) -> frozenset[str] | None:
        raw = os.environ.get(_TRUSTED_HOSTS_ENV, "").strip()
        if not raw:
            return None
        hosts = frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
        return hosts or None

    def _host_matches(self, host: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if pattern.startswith("*."):
            suffix = pattern[1:]
            return host.endswith(suffix) and host != pattern[2:]
        return host == pattern


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Stamp baseline browser-facing security headers on every response."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response


_API_ROUTERS = (
    claims_router,
    canonical_router,
    ingestion_router,
    verification_router,
    human_review_router,
    query_router,
    stats_router,
    audit_router,
)


app = FastAPI(title="Ayiru", version="0.1.0")
app.add_middleware(RequestBodySizeLimitMiddleware)
app.add_middleware(LegacyDeprecationHeaderMiddleware)
app.add_middleware(AskRateLimitMiddleware)
# Auth gate. No-op when `AYIRU_API_KEY` is unset (dev default).
app.add_middleware(ApiKeyAuthMiddleware)
app.add_middleware(TrustedHostGuardMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# Observability is registered last so it wraps every other middleware
# layer — request IDs cover body-size, deprecation, auth, and host-guard
# paths too.
app.add_middleware(RequestObservabilityMiddleware)

# Health is universal; never versioned.
app.include_router(health_router)

# Canonical /v1 mount — the contract clients should target.
for router in _API_ROUTERS:
    app.include_router(router, prefix="/v1")

# Legacy mount (un-versioned, deprecated). Removed in 0.2.0; transition
# window documented in CONTRIBUTING.md and the Stage 14 stage_report.
for router in _API_ROUTERS:
    app.include_router(router)


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
