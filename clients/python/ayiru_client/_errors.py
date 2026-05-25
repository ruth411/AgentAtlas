"""Shared exception type for both sync and async clients.

The SDK never returns the raw httpx error — it wraps every server-side
4xx/5xx in :class:`AyiruError` with the structured server envelope
(``code``, ``message``, ``details``) attached so caller code can branch
on a stable error code without parsing strings."""

from __future__ import annotations

from typing import Any


class AyiruError(Exception):
    """Raised when the Ayiru server returns a non-success response.

    Attributes:
        status_code: HTTP status code from the server response.
        code: Server-side error code from the JSON envelope (e.g.
            ``"CANONICAL_SPEC_NOT_FOUND"``). Empty if the body was not a
            structured envelope.
        message: Human-readable server message.
        details: Structured details dict; empty when the server didn't
            include one.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str = "",
        message: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(self._format())

    def _format(self) -> str:
        head = f"AyiruError {self.status_code}"
        if self.code:
            head += f" [{self.code}]"
        if self.message:
            head += f": {self.message}"
        return head


def parse_error_payload(payload: Any) -> tuple[str, str, dict[str, Any]]:
    """Pull (code, message, details) from the server's error envelope.

    Accepts the structured ``{"error": {...}}`` envelope the FastAPI
    layer emits as well as malformed bodies (returns empty fields)."""

    if not isinstance(payload, dict):
        return "", "", {}
    err = payload.get("error")
    if not isinstance(err, dict):
        return "", "", {}
    code = str(err.get("code") or "")
    message = str(err.get("message") or "")
    details = err.get("details")
    return code, message, details if isinstance(details, dict) else {}


__all__ = ["AyiruError", "parse_error_payload"]
