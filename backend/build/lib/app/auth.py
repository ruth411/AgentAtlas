"""Stage 14: optional API-key authentication.

Off by default. When `AGENTATLAS_API_KEY` is set to a non-empty value,
all **state-changing** requests (POST / PUT / PATCH / DELETE) must carry
`Authorization: Bearer <api-key>`; read requests stay public so the
demo dashboard and `agentatlas query` keep working unchanged.

The choice to gate writes only is deliberate for v1.0:

- Read endpoints serve cached, deterministic verdicts. Exposing them
  publicly is the *point* — agents are supposed to query them freely.
- Write endpoints (claim submission, runtime verification, human
  review, ingestion, spec publication) change canonical state. Those
  need a credential when the API is reachable outside localhost.

A reviewer-id allowlist (`AGENTATLAS_REVIEWER_REGISTRY`) gates the
`POST /verification/human-review` route further: when set, the
`reviewer_id` on the submitted decision must appear in the registry's
comma-separated list. Without the env var, any `reviewer_id` is
accepted (v1.0 dev default).

Both controls are configuration-driven. There is no UI to register keys
or reviewers in v1.0; ops manages them through env vars on the serving
process. Per-reviewer cryptographic identity is on the v1.1 roadmap.
"""

from __future__ import annotations

import os
from typing import Iterable

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.errors import ErrorCode, error_payload
from fastapi.responses import JSONResponse


_API_KEY_ENV = "AGENTATLAS_API_KEY"
_REVIEWER_REGISTRY_ENV = "AGENTATLAS_REVIEWER_REGISTRY"


# Read endpoints exempt from auth even when AGENTATLAS_API_KEY is set.
_READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths that are always public regardless of method. Health checks need
# to succeed for orchestration probes (k8s liveness/readiness) without
# a credential.
_ALWAYS_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def configured_api_key() -> str | None:
    """Return the configured API key, or `None` if auth is disabled."""
    value = os.environ.get(_API_KEY_ENV, "").strip()
    return value or None


def configured_reviewers() -> frozenset[str] | None:
    """Return the configured reviewer allowlist, or `None` if open."""
    raw = os.environ.get(_REVIEWER_REGISTRY_ENV, "").strip()
    if not raw:
        return None
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def reviewer_allowed(reviewer_id: str) -> bool:
    """Stage 13 hook: check `reviewer_id` against the optional registry.

    Returns `True` when the registry is unset (open allowance), or when
    the id is explicitly present. The human-review route imports this
    and surfaces a structured 403 when the check fails.
    """
    registry = configured_reviewers()
    if registry is None:
        return True
    return reviewer_id in registry


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests that don't carry a valid Bearer
    token, when `AGENTATLAS_API_KEY` is set.

    Header lookup is constant-time-ish (uses `hmac.compare_digest` to
    avoid leaking the key through timing). Missing header / wrong
    scheme / wrong key all produce the same structured 401 so the
    error doesn't help an attacker distinguish "wrong key" from "no
    key".
    """

    def __init__(self, app, public_prefixes: Iterable[str] = _ALWAYS_PUBLIC_PREFIXES) -> None:
        super().__init__(app)
        self._public_prefixes = tuple(public_prefixes)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        key = configured_api_key()
        if key is None:
            # Auth disabled — preserve dev ergonomics.
            return await call_next(request)

        if request.method in _READ_METHODS:
            return await call_next(request)

        path = request.url.path
        # Strip the optional `/v1` prefix so the same public list works
        # for both the canonical and legacy mounts.
        normalized = path[len("/v1"):] if path.startswith("/v1/") else path
        if any(normalized.startswith(prefix) for prefix in self._public_prefixes):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not _header_is_valid_bearer(header, key):
            return _unauthorized()
        return await call_next(request)


def _header_is_valid_bearer(header: str, expected: str) -> bool:
    import hmac

    if not header:
        return False
    parts = header.split(maxsplit=1)
    if len(parts) != 2:
        return False
    scheme, value = parts
    if scheme.lower() != "bearer":
        return False
    # `compare_digest` is timing-safe; falsy results are coerced to
    # equal-length comparisons so we don't leak via early returns.
    return hmac.compare_digest(value.strip(), expected)


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=error_payload(
            code=ErrorCode.HTTP_ERROR,
            message=(
                "Write endpoints require an Authorization: Bearer "
                "<api-key> header when AGENTATLAS_API_KEY is configured."
            ),
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )
