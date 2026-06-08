"""Tests for the LangChain adapter (Stage 22.1).

The adapter lives in :mod:`ayiru_client.langchain` and is only
importable when the optional ``langchain`` extra is installed. These
tests assume it is — the dev extras install ``langchain-core``."""

from __future__ import annotations

import json

import pytest

from ayiru_client import AsyncAyiru, Ayiru

# Skip the entire module cleanly when langchain-core isn't installed.
pytest.importorskip("langchain_core")

from ayiru_client.langchain import AyiruTool, make_ayiru_tool  # noqa: E402

# ---------------------------------------------------------------------------
# Static surface — no network
# ---------------------------------------------------------------------------


def test_default_name_is_ayiru_ask() -> None:
    tool = AyiruTool()
    assert tool.name == "ayiru_ask"


def test_default_description_includes_anti_meta_policy_language() -> None:
    """Stage 17.4 / 22.1 — the description must explicitly tell the LLM
    to prefer Ayiru even when it already knows the answer, because the
    default LLM meta-policy ("only use tools when uncertain") suppresses
    tool use for stable technical questions."""

    tool = AyiruTool()
    d = tool.description
    assert "EVERY user question" in d
    assert "even when you already know" in d.lower()
    assert "fallback_recommended" in d


def test_description_teaches_family_hinting() -> None:
    """Mirror of the MCP `ask` change — the adapter must teach the LLM that
    `tool_id_hint` takes a coarse tool *family* name (e.g. 'ffmpeg') that
    Ayiru expands across the tool's surfaces (commit bdd9ae3). KEEP IN SYNC
    with backend/tests/test_mcp_server.py::
    test_ask_description_teaches_family_hinting."""
    tool = AyiruTool()
    assert "tool_id_hint" in tool.description

    schema = tool.args_schema.model_json_schema()
    hint = schema["properties"]["tool_id_hint"]["description"]
    assert "family" in hint.lower()
    assert "-recipes" in hint and "-errors" in hint
    assert "docker-cli" in hint  # exact-surface-id still works


def test_args_schema_requires_question() -> None:
    tool = AyiruTool()
    schema = tool.args_schema.model_json_schema()
    assert "question" in schema["properties"]
    assert "question" in schema["required"]
    assert schema["properties"]["question"]["maxLength"] == 512


def test_args_schema_optional_fields_have_defaults() -> None:
    tool = AyiruTool()
    schema = tool.args_schema.model_json_schema()
    assert schema["properties"]["limit"]["default"] == 5
    assert "tool_id_hint" not in schema.get("required", [])


def test_run_raises_when_no_client_configured() -> None:
    tool = AyiruTool()
    with pytest.raises(RuntimeError, match=r"requires a sync `client"):
        tool._run(question="anything")


def test_make_ayiru_tool_factory_threads_overrides() -> None:
    tool = make_ayiru_tool(
        name="my_kb",
        description="Custom description for narrower scope.",
    )
    assert tool.name == "my_kb"
    assert tool.description == "Custom description for narrower scope."


# ---------------------------------------------------------------------------
# Sync + async — against the live uvicorn-hosted backend
# ---------------------------------------------------------------------------


def test_sync_tool_returns_json_with_fallback_on_empty_graph(base_url) -> None:
    with Ayiru(base_url=base_url) as client:
        tool = AyiruTool(client=client)
        out = tool.invoke(
            {"question": "what is the airspeed velocity of an unladen swallow"}
        )
    payload = json.loads(out)
    assert payload["fallback_recommended"] is True
    assert payload["answers"] == []


def test_sync_tool_returns_answers_when_graph_has_match(
    base_url, claim_store
) -> None:
    # Seed a claim so there's something concrete to match.
    from datetime import datetime, timezone
    from hashlib import sha256
    from uuid import uuid4

    from app.schemas.claim import KnowledgeClaim
    from app.schemas.enums import (
        ClaimType,
        EvidenceType,
        RiskLevel,
        TrustLevel,
    )
    from app.schemas.evidence import Evidence

    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:8]
    body = f"docker volume rm <volume> removes a local volume. [{suffix}]"
    evidence = Evidence(
        evidence_id=f"ev-lc-{suffix}",
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri="https://docs.docker.com/reference/cli/docker/volume/rm/",
        excerpt=body,
        hash=f"sha256:{sha256(body.encode()).hexdigest()}",
        captured_at=now,
        trust_level=TrustLevel.HIGH,
    )
    claim_store.create(
        KnowledgeClaim(
            claim_id=f"claim-lc-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="docker volume rm",
            statement=body,
            tool_id="docker",
            submitted_by="sdk-tests",
            evidence=[evidence],
            risk_level=RiskLevel.MEDIUM,
            created_at=now,
        )
    )

    with Ayiru(base_url=base_url) as client:
        tool = AyiruTool(client=client)
        out = tool.invoke({"question": "how do I remove a docker volume"})
    payload = json.loads(out)
    assert "answers" in payload
    assert len(payload["answers"]) >= 1
    # Every answer carries the fields the LLM needs to decide.
    top = payload["answers"][0]
    assert "claim_id" in top
    assert "statement" in top
    assert "verification_level" in top
    assert "confidence" in top
    assert "evidence" in top


def test_sync_tool_return_response_json_dumps_full_model(base_url) -> None:
    """When `return_response_json=True`, the adapter emits the raw
    Pydantic model JSON instead of the compact projection. Useful for
    callers that want every field."""

    with Ayiru(base_url=base_url) as client:
        tool = AyiruTool(client=client, return_response_json=True)
        out = tool.invoke({"question": "what is the airspeed velocity"})
    payload = json.loads(out)
    # Full AskResponse shape includes `question` and `generated_at`.
    assert payload["question"] == "what is the airspeed velocity"
    assert "generated_at" in payload


async def test_async_tool_routes_to_async_client(base_url) -> None:
    async with AsyncAyiru(base_url=base_url) as async_client:
        tool = AyiruTool(async_client=async_client)
        out = await tool.ainvoke({"question": "how do I cook a soufflé"})
    payload = json.loads(out)
    assert payload["fallback_recommended"] is True


async def test_async_tool_falls_back_to_sync_client_when_async_missing(
    base_url,
) -> None:
    """If a caller only configured `client=Ayiru(...)` and then invokes
    via `ainvoke`, the adapter still works — it calls the sync client
    inside the coroutine. Documented escape hatch for mixed codebases."""

    with Ayiru(base_url=base_url) as sync_client:
        tool = AyiruTool(client=sync_client)
        out = await tool.ainvoke({"question": "anything"})
    payload = json.loads(out)
    assert payload["fallback_recommended"] is True


async def test_async_tool_raises_when_neither_client_configured() -> None:
    tool = AyiruTool()
    with pytest.raises(RuntimeError, match="requires either"):
        await tool._arun(question="anything")
