"""Async :class:`AsyncAyiru` client.

Mirror of the sync :class:`~ayiru_client._client.Ayiru` class — same
methods, same response models, same auth pattern, same exception type.
The implementations diverge only on the httpx primitive used."""

from __future__ import annotations

from typing import Any

import httpx

from ayiru_client._errors import AyiruError, parse_error_payload
from ayiru_client._models import (
    AskResponse,
    ConstraintSetResponse,
    EffectProfileResponse,
    GetCapabilitiesResponse,
    ResolveActionResponse,
    ResolveSubjectResponse,
    SavingsResponse,
    SearchToolsResponse,
    SubjectSpecResponse,
    ToolSpec,
    ValidateCommandResponse,
    WorkflowPlanResponse,
)
from ayiru_client._version import USER_AGENT

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class AsyncAyiru:
    """Async client.

    Example:
        >>> import asyncio
        >>> from ayiru_client import AsyncAyiru
        >>>
        >>> async def main():
        ...     async with AsyncAyiru(base_url="http://localhost:8000") as client:
        ...         answer = await client.ask("how do I delete a docker volume")
        ...         if answer.is_useful:
        ...             print(answer.top.statement)
        >>> asyncio.run(main())

    Mirrors :class:`Ayiru` exactly; see its docstring for argument docs.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: httpx.Timeout | float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(
            base_url=f"{self._base_url}/v1",
            headers=headers,
            timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
            transport=transport,
        )

    async def __aenter__(self) -> AsyncAyiru:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Public methods (mirror Ayiru)
    # ------------------------------------------------------------------

    async def ask(
        self,
        question: str,
        *,
        limit: int = 5,
        tool_id_hint: str | None = None,
    ) -> AskResponse:
        body: dict[str, Any] = {"question": question, "limit": limit}
        if tool_id_hint:
            body["tool_id_hint"] = tool_id_hint
        data = await self._post("/query/ask", json=body)
        return AskResponse.model_validate(data)

    async def validate_command(
        self, *, tool_id: str, command: str
    ) -> ValidateCommandResponse:
        data = await self._post(
            "/query/validate-command",
            json={"tool_id": tool_id, "command": command},
        )
        return ValidateCommandResponse.model_validate(data)

    async def get_tool_spec(self, tool_id: str) -> ToolSpec:
        data = await self._get(f"/query/tools/{tool_id}")
        return ToolSpec.model_validate(data)

    async def resolve_subject(
        self,
        subject_hint: str,
        *,
        kind: str | None = None,
        family_hint: str | None = None,
        limit: int = 20,
    ) -> ResolveSubjectResponse:
        body: dict[str, Any] = {"subject_hint": subject_hint, "limit": limit}
        if kind is not None:
            body["kind"] = kind
        if family_hint is not None:
            body["family_hint"] = family_hint
        data = await self._post("/query/resolve-subject", json=body)
        return ResolveSubjectResponse.model_validate(data)

    async def get_subject_spec(self, subject_id: str) -> SubjectSpecResponse:
        data = await self._get(f"/query/subjects/{subject_id}")
        return SubjectSpecResponse.model_validate(data)

    async def get_capabilities(
        self,
        subject_id: str,
        *,
        capability_types: list[str] | None = None,
        accepted_only: bool = True,
        accepted_only_structured: bool = False,
        verification_min: str | None = None,
        limit: int = 50,
    ) -> GetCapabilitiesResponse:
        body: dict[str, Any] = {
            "subject_id": subject_id,
            "accepted_only": accepted_only,
            "accepted_only_structured": accepted_only_structured,
            "limit": limit,
        }
        if capability_types is not None:
            body["capability_types"] = capability_types
        if verification_min is not None:
            body["verification_min"] = verification_min
        data = await self._post("/query/capabilities", json=body)
        return GetCapabilitiesResponse.model_validate(data)

    async def get_constraints(
        self,
        subject_id: str,
        *,
        action_intent: str | None = None,
        accepted_only: bool = True,
        accepted_only_structured: bool = False,
    ) -> ConstraintSetResponse:
        body: dict[str, Any] = {
            "subject_id": subject_id,
            "accepted_only": accepted_only,
            "accepted_only_structured": accepted_only_structured,
        }
        if action_intent is not None:
            body["action_intent"] = action_intent
        data = await self._post("/query/constraints", json=body)
        return ConstraintSetResponse.model_validate(data)

    async def get_effects(
        self,
        subject_id: str,
        *,
        action_intent: str | None = None,
        accepted_only: bool = True,
        accepted_only_structured: bool = False,
    ) -> EffectProfileResponse:
        body: dict[str, Any] = {
            "subject_id": subject_id,
            "accepted_only": accepted_only,
            "accepted_only_structured": accepted_only_structured,
        }
        if action_intent is not None:
            body["action_intent"] = action_intent
        data = await self._post("/query/effects", json=body)
        return EffectProfileResponse.model_validate(data)

    async def resolve_action(
        self,
        subject_id: str,
        action_intent: str,
        *,
        command: str | None = None,
        environment: str | None = None,
        accepted_only: bool = True,
        accepted_only_structured: bool = False,
        limit: int = 5,
    ) -> ResolveActionResponse:
        body: dict[str, Any] = {
            "subject_id": subject_id,
            "action_intent": action_intent,
            "accepted_only": accepted_only,
            "accepted_only_structured": accepted_only_structured,
            "limit": limit,
        }
        if command is not None:
            body["command"] = command
        if environment is not None:
            body["environment"] = environment
        data = await self._post("/query/resolve-action", json=body)
        return ResolveActionResponse.model_validate(data)

    async def get_workflow_plan(
        self,
        goal: str,
        *,
        environment: str | None = None,
        limit: int = 20,
    ) -> WorkflowPlanResponse:
        body: dict[str, Any] = {"goal": goal, "limit": limit}
        if environment is not None:
            body["environment"] = environment
        data = await self._post("/query/workflow-plan", json=body)
        return WorkflowPlanResponse.model_validate(data)

    async def search_tools(
        self,
        query: str = "",
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> SearchToolsResponse:
        data = await self._get(
            "/query/search-tools",
            params={"q": query, "limit": limit, "offset": offset},
        )
        return SearchToolsResponse.model_validate(data)

    async def savings(self, window: str = "all") -> SavingsResponse:
        data = await self._get("/stats/savings", params={"window": window})
        return SavingsResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Internal HTTP plumbing
    # ------------------------------------------------------------------

    async def _get(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._send("GET", path, params=params)

    async def _post(self, path: str, *, json: dict[str, Any]) -> Any:
        return await self._send("POST", path, json=json)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method, path, params=params, json=json
            )
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


__all__ = ["AsyncAyiru"]
