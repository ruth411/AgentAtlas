"""Synchronous :class:`Ayiru` client.

The blocking variant — the simplest possible entry point for a script
or a notebook. Wraps :mod:`httpx.Client` and delegates response shape
to the models in :mod:`ayiru_client._models`.

The async twin lives in :mod:`ayiru_client._async_client` and shares
the exception type, the URL plan, and the auth pattern."""

from __future__ import annotations

from typing import Any

import httpx

from ayiru_client._errors import AyiruError, parse_error_payload
from ayiru_client._models import (
    AskResponse,
    SavingsResponse,
    SearchToolsResponse,
    ToolSpec,
    ValidateCommandResponse,
)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_USER_AGENT = "ayiru-client-py/0.2.0"


class Ayiru:
    """Synchronous client.

    Example:
        >>> from ayiru_client import Ayiru
        >>> client = Ayiru(base_url="http://localhost:8000")
        >>> answer = client.ask("how do I delete a docker volume")
        >>> if answer.is_useful:
        ...     print(answer.top.statement)

    Args:
        base_url: Origin of the Ayiru server, e.g. ``http://localhost:8000``.
            The SDK appends ``/v1`` automatically — don't include it.
        api_key: Bearer token sent on every request when set. Required only
            for write endpoints when the server has ``AYIRU_API_KEY``
            configured; read endpoints (``ask``, ``validate_command``,
            ``get_tool_spec``, ``search_tools``, ``savings``) work without
            one.
        timeout: Per-request timeout. Defaults to a generous 30s read.
        transport: Optional httpx transport (used in tests to wrap an
            ASGI app via :class:`httpx.ASGITransport`).
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: httpx.Timeout | float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(
            base_url=f"{self._base_url}/v1",
            headers=headers,
            timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
            transport=transport,
        )

    def __enter__(self) -> Ayiru:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def ask(
        self,
        question: str,
        *,
        limit: int = 5,
        tool_id_hint: str | None = None,
    ) -> AskResponse:
        """Natural-language query against the verified knowledge graph."""

        body: dict[str, Any] = {"question": question, "limit": limit}
        if tool_id_hint:
            body["tool_id_hint"] = tool_id_hint
        data = self._post("/query/ask", json=body)
        return AskResponse.model_validate(data)

    def validate_command(
        self, *, tool_id: str, command: str
    ) -> ValidateCommandResponse:
        """Lookup a specific command verdict — risk + auto-execute safety."""

        data = self._post(
            "/query/validate-command",
            json={"tool_id": tool_id, "command": command},
        )
        return ValidateCommandResponse.model_validate(data)

    def get_tool_spec(self, tool_id: str) -> ToolSpec:
        """Fetch the canonical ToolSpec for a tool. Raises AyiruError(404)
        when no spec has been published for that tool_id."""

        return self._get(f"/query/tools/{tool_id}")  # type: ignore[return-value]

    def search_tools(
        self,
        query: str = "",
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> SearchToolsResponse:
        """List or search for tools known to the graph."""

        data = self._get(
            "/query/search-tools",
            params={"q": query, "limit": limit, "offset": offset},
        )
        return SearchToolsResponse.model_validate(data)

    def savings(self, window: str = "all") -> SavingsResponse:
        """Aggregate cost savings over the QUERY_SERVED audit stream.

        ``window`` is one of ``"24h"``, ``"7d"``, ``"30d"``, ``"all"``."""

        data = self._get("/stats/savings", params={"window": window})
        return SavingsResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Internal HTTP plumbing
    # ------------------------------------------------------------------

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._send("GET", path, params=params)

    def _post(self, path: str, *, json: dict[str, Any]) -> Any:
        return self._send("POST", path, json=json)

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._http.request(method, path, params=params, json=json)
        except httpx.RequestError as exc:
            raise AyiruError(
                status_code=0,
                code="TRANSPORT_ERROR",
                message=str(exc),
            ) from exc
        if response.status_code >= 400:
            self._raise_from(response)
        if not response.content:
            return None
        return response.json()

    def _raise_from(self, response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        code, message, details = parse_error_payload(payload)
        raise AyiruError(
            status_code=response.status_code,
            code=code,
            message=message or response.reason_phrase or "",
            details=details,
        )


__all__ = ["Ayiru"]
