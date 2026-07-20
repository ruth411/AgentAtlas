"""SDK integration tests against the real backend app.

When the environment permits local socket binds these run against a live
uvicorn-hosted backend. In restricted sandboxes they fall back to
in-process transports, but still exercise the real FastAPI app and the
real SDK parsing / error-handling code."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest

from ayiru_client import (
    Answer,
    AskResponse,
    AyiruError,
    ConstraintSetResponse,
    EffectProfileResponse,
    GetCapabilitiesResponse,
    ResolveActionResponse,
    ResolveSubjectResponse,
    SearchToolsResponse,
    SubjectSpecResponse,
    ToolSpec,
    ValidateCommandResponse,
    WorkflowPlanResponse,
    __version__,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_minimal_claim(store, *, subject: str = "docker volume rm") -> str:
    """Insert one verified claim with a unique id so per-test inserts
    don't collide. Returns the claim_id for the caller to assert on."""

    from app.schemas.claim import KnowledgeClaim  # noqa: PLC0415
    from app.schemas.confidence import (  # noqa: PLC0415
        ConfidenceBreakdown,
        ConfidenceComponent,
    )
    from app.schemas.enums import (  # noqa: PLC0415
        ClaimType,
        ConfidenceBand,
        EvidenceType,
        OrchestratorDecision,
        RiskLevel,
        TrustLevel,
        VerificationLevel,
        VerificationStatus,
    )
    from app.schemas.evidence import Evidence  # noqa: PLC0415
    from app.schemas.verification import VerificationResult  # noqa: PLC0415
    from tests.helpers import risk_assessment  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:8]
    body = f"{subject} <volume> removes a local volume. [{suffix}]"
    evidence = Evidence(
        evidence_id=f"ev-test-{suffix}",
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri="https://docs.docker.com/reference/cli/docker/volume/rm/",
        excerpt=body,
        hash=f"sha256:{sha256(body.encode()).hexdigest()}",
        captured_at=now,
        trust_level=TrustLevel.HIGH,
    )
    claim = KnowledgeClaim(
        claim_id=f"claim-test-{suffix}",
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject=subject,
        statement=body,
        tool_id="docker",
        submitted_by="sdk-tests",
        evidence=[evidence],
        risk_level=RiskLevel.MEDIUM,
        created_at=now,
    )
    store.create(claim)
    store.save_verification_result(
        VerificationResult(
            verification_id=f"vr-test-{suffix}",
            claim_id=claim.claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            confidence=0.92,
            confidence_band=ConfidenceBand.STRONG,
            confidence_breakdown=ConfidenceBreakdown(
                score=0.92,
                band=ConfidenceBand.STRONG,
                components=[
                    ConfidenceComponent(
                        source="test",
                        delta=0.92,
                        reason="sdk integration seed",
                    )
                ],
                caps_applied=[],
                penalties=[],
            ),
            risk_assessment=risk_assessment(RiskLevel.MEDIUM),
            reason_codes=["TEST_ACCEPTED"],
            reasons=["seeded as an accepted claim for SDK tests"],
            verified_at=now,
        )
    )
    return claim.claim_id


def _publish_tool_spec(store, *, tool_id: str = "docker") -> None:
    from app.schemas.enums import RiskLevel, VerificationLevel  # noqa: PLC0415
    from app.schemas.tool_spec import (  # noqa: PLC0415
        AuthRequirement,
        Provenance,
        RiskProfile,
        ToolSpec,
    )

    now = datetime.now(timezone.utc)
    store.save_canonical_tool_spec(
        ToolSpec(
            tool_id=tool_id,
            name=tool_id.title(),
            interfaces=["cli"],
            capabilities=["read", "execute"],
            commands=[],
            auth=AuthRequirement(required=False, methods=[]),
            risk_profile=RiskProfile(default_risk_level=RiskLevel.LOW),
            provenance=Provenance(
                source_claim_ids=["c1"],
                source_evidence_ids=["e1"],
                compiled_at=now,
                compiled_by="sdk-tests",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            ),
            workflows=["workflow-test"],
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        )
    )


def _publish_workflow(store, *, workflow_id: str = "workflow-test", goal: str = "deploy preview") -> None:
    from app.schemas.enums import RiskLevel, VerificationLevel  # noqa: PLC0415
    from app.schemas.tool_spec import Provenance  # noqa: PLC0415
    from app.schemas.workflow_spec import WorkflowSpec, WorkflowStep  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    store.save_canonical_workflow_spec(
        WorkflowSpec(
            workflow_id=workflow_id,
            goal=goal,
            tool_ids=["docker"],
            steps=[
                WorkflowStep(
                    step_id="s1",
                    action="build preview",
                    description="Build the preview artifact.",
                    risk_level=RiskLevel.LOW,
                )
            ],
            provenance=Provenance(
                source_claim_ids=["c1"],
                source_evidence_ids=["e1"],
                compiled_at=now,
                compiled_by="sdk-tests",
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
            ),
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        )
    )


# ---------------------------------------------------------------------------
# Sync client — over a real HTTP socket
# ---------------------------------------------------------------------------


def test_sync_ask_returns_typed_response(sync_client_factory, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    with sync_client_factory() as client:
        resp = client.ask("how do I remove a docker volume")
    assert isinstance(resp, AskResponse)
    assert resp.question == "how do I remove a docker volume"
    assert len(resp.answers) >= 1


def test_sync_ask_is_useful_property(sync_client_factory, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    with sync_client_factory() as client:
        resp = client.ask("how do I remove a docker volume")
    top = resp.top
    assert top is not None
    expected = (
        top.verification_status == "accepted"
        and top.confidence >= 0.6
        and top.verification_level != "L0_unverified"
    )
    assert top.is_useful is expected


def test_sync_ask_on_unknown_topic_recommends_fallback(sync_client_factory) -> None:
    with sync_client_factory() as client:
        resp = client.ask("what is the airspeed velocity of an unladen swallow")
    assert resp.fallback_recommended is True
    assert resp.is_useful is False
    assert resp.estimated_tokens_saved == 0


def test_sync_search_tools_returns_typed_response(sync_client_factory) -> None:
    with sync_client_factory() as client:
        resp = client.search_tools()
    assert isinstance(resp, SearchToolsResponse)
    assert resp.limit >= 1
    assert resp.offset == 0


def test_sync_validate_command_returns_typed_response(
    sync_client_factory, claim_store
) -> None:
    _seed_minimal_claim(claim_store)
    with sync_client_factory() as client:
        resp = client.validate_command(
            tool_id="docker", command="docker volume rm my-volume"
        )
    assert isinstance(resp, ValidateCommandResponse)
    assert resp.tool_id == "docker"


def test_sync_structured_subject_and_capability_methods(
    sync_client_factory, claim_store
) -> None:
    _seed_minimal_claim(claim_store)
    _publish_tool_spec(claim_store)
    with sync_client_factory() as client:
        subject = client.resolve_subject("docker")
        spec = client.get_subject_spec("docker")
        capabilities = client.get_capabilities("docker", accepted_only=False)
        constraints = client.get_constraints("docker")
        effects = client.get_effects("docker")
    assert isinstance(subject, ResolveSubjectResponse)
    assert any(match.subject_id == "docker" for match in subject.matches)
    assert isinstance(spec, SubjectSpecResponse)
    assert spec.subject_id == "docker"
    assert isinstance(capabilities, GetCapabilitiesResponse)
    assert capabilities.total >= 1
    assert isinstance(constraints, ConstraintSetResponse)
    assert constraints.subject_id == "docker"
    assert isinstance(effects, EffectProfileResponse)
    assert effects.subject_id == "docker"


def test_sync_resolve_action_and_workflow_plan(sync_client_factory, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    _publish_tool_spec(claim_store)
    _publish_workflow(claim_store, goal="deploy preview sdk test")
    with sync_client_factory() as client:
        command_resolution = client.resolve_action(
            "docker",
            "remove a local volume",
            command="docker volume rm my-volume",
        )
        plan = client.get_workflow_plan("deploy preview sdk test")
    assert isinstance(command_resolution, ResolveActionResponse)
    assert command_resolution.resolution_mode == "command_match"
    assert command_resolution.subject_id == "docker"
    assert isinstance(plan, WorkflowPlanResponse)
    assert any(item.goal == "deploy preview sdk test" for item in plan.plans)


def test_sync_get_tool_spec_returns_typed_response(
    sync_client_factory, claim_store
) -> None:
    _publish_tool_spec(claim_store)
    with sync_client_factory() as client:
        spec = client.get_tool_spec("docker")
    assert isinstance(spec, ToolSpec)
    assert spec.tool_id == "docker"
    assert spec.risk_profile.default_risk_level == "low"


def test_sync_get_tool_spec_raises_on_unknown(sync_client_factory) -> None:
    with sync_client_factory() as client, pytest.raises(AyiruError) as exc:
        client.get_tool_spec("definitely-not-a-real-tool")
    assert exc.value.status_code == 404
    assert exc.value.code == "CANONICAL_SPEC_NOT_FOUND"


def test_sync_savings_returns_typed_response(sync_client_factory) -> None:
    with sync_client_factory() as client:
        resp = client.savings("7d")
    assert resp.window == "7d"
    assert resp.total_queries_served >= 0


def test_sync_api_key_attaches_bearer_header(sync_client_factory) -> None:
    with sync_client_factory(api_key="test-key") as client:
        assert client._http.headers["Authorization"] == "Bearer test-key"


def test_sync_no_api_key_omits_bearer_header(sync_client_factory) -> None:
    with sync_client_factory() as client:
        assert "Authorization" not in client._http.headers


def test_sync_user_agent_identifies_sdk(sync_client_factory) -> None:
    with sync_client_factory() as client:
        assert client._http.headers["User-Agent"] == f"ayiru-client-py/{__version__}"


def test_exported_version_matches_installed_package_metadata() -> None:
    from importlib import metadata

    assert __version__ == metadata.version("ayiru-client")


async def test_async_api_key_attaches_bearer_header(async_client_factory) -> None:
    async with async_client_factory(api_key="test-key") as client:
        assert client._http.headers["Authorization"] == "Bearer test-key"


async def test_async_no_api_key_omits_bearer_header(async_client_factory) -> None:
    async with async_client_factory() as client:
        assert "Authorization" not in client._http.headers


async def test_async_user_agent_identifies_sdk(async_client_factory) -> None:
    async with async_client_factory() as client:
        assert client._http.headers["User-Agent"] == f"ayiru-client-py/{__version__}"


def test_package_version_falls_back_when_metadata_missing(monkeypatch) -> None:
    from importlib import metadata

    from ayiru_client import _version

    def missing(_dist_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(_version.metadata, "version", missing)
    assert _version.package_version() == "0.0.0+unknown"


# ---------------------------------------------------------------------------
# Async client — same server, same DB
# ---------------------------------------------------------------------------


async def test_async_ask_returns_typed_response(async_client_factory, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    async with async_client_factory() as client:
        resp = await client.ask("how do I remove a docker volume")
    assert isinstance(resp, AskResponse)
    assert len(resp.answers) >= 1


async def test_async_ask_on_unknown_topic_recommends_fallback(async_client_factory) -> None:
    async with async_client_factory() as client:
        resp = await client.ask("how do I cook a soufflé")
    assert resp.fallback_recommended is True
    assert resp.is_useful is False


async def test_async_search_tools(async_client_factory) -> None:
    async with async_client_factory() as client:
        resp = await client.search_tools()
    assert isinstance(resp, SearchToolsResponse)


async def test_async_structured_methods(async_client_factory, claim_store) -> None:
    _seed_minimal_claim(claim_store)
    _publish_tool_spec(claim_store)
    _publish_workflow(claim_store, goal="deploy preview async sdk test")
    async with async_client_factory() as client:
        subject = await client.resolve_subject("docker")
        spec = await client.get_subject_spec("docker")
        capabilities = await client.get_capabilities("docker", accepted_only=False)
        resolution = await client.resolve_action("docker", "remove a local volume")
        plan = await client.get_workflow_plan("deploy preview async sdk test")
    assert isinstance(subject, ResolveSubjectResponse)
    assert any(match.subject_id == "docker" for match in subject.matches)
    assert isinstance(spec, SubjectSpecResponse)
    assert spec.subject_id == "docker"
    assert isinstance(capabilities, GetCapabilitiesResponse)
    assert capabilities.total >= 1
    assert isinstance(resolution, ResolveActionResponse)
    assert resolution.subject_id == "docker"
    assert isinstance(plan, WorkflowPlanResponse)
    assert any(item.goal == "deploy preview async sdk test" for item in plan.plans)


async def test_async_get_tool_spec_returns_typed_response(
    async_client_factory, claim_store
) -> None:
    _publish_tool_spec(claim_store)
    async with async_client_factory() as client:
        spec = await client.get_tool_spec("docker")
    assert isinstance(spec, ToolSpec)
    assert spec.tool_id == "docker"
    assert spec.risk_profile.default_risk_level == "low"


async def test_async_get_tool_spec_raises_on_unknown(async_client_factory) -> None:
    async with async_client_factory() as client:
        with pytest.raises(AyiruError) as exc:
            await client.get_tool_spec("nope")
    assert exc.value.status_code == 404


async def test_async_savings(async_client_factory) -> None:
    async with async_client_factory() as client:
        resp = await client.savings("all")
    assert resp.window == "all"


# ---------------------------------------------------------------------------
# Models (no network)
# ---------------------------------------------------------------------------


def test_answer_is_useful_threshold() -> None:
    base = dict(
        claim_id="c1",
        subject="x",
        statement="y",
        tool_id="docker",
        confidence_band="moderate",
        verification_status="accepted",
        evidence=[],
        match_reason="exact",
    )
    assert Answer(
        confidence=0.6, verification_level="L2_source_verified", **base
    ).is_useful is True
    assert Answer(
        confidence=0.59, verification_level="L2_source_verified", **base
    ).is_useful is False
    assert Answer(
        confidence=0.95, verification_level="L0_unverified", **base
    ).is_useful is False


def test_answer_accepts_risk_level_none() -> None:
    """Regression for P0-1 (2026-05-26 second-pass audit): the server's
    RiskLevel.NONE value is emitted as the string "none" for no-risk
    claims (e.g. read-only queries cleared by safety_policy.py). The
    SDK must accept it as a valid wire value alongside the other four
    risk levels."""

    answer = Answer(
        claim_id="c1",
        subject="git log",
        statement="git log shows commit history.",
        tool_id="git",
        confidence=0.9,
        confidence_band="high",
        verification_status="accepted",
        verification_level="L2_source_verified",
        risk_level="none",
        evidence=[],
        match_reason="exact",
    )
    assert answer.risk_level == "none"


def test_ask_response_top_is_none_on_empty_answers() -> None:
    resp = AskResponse(
        question="q",
        answers=[],
        answer_status="miss",
        fallback_recommended=True,
        estimated_tokens_saved=0,
        generated_at=datetime.now(timezone.utc),
    )
    assert resp.top is None
    assert resp.is_useful is False


def test_get_capabilities_response_accepts_structured_detail_payload() -> None:
    response = GetCapabilitiesResponse.model_validate(
        {
            "subject_id": "gh-pr-create",
            "accepted_only": True,
            "accepted_only_structured": False,
            "total": 1,
            "limit": 50,
            "capabilities": [
                {
                    "capability_id": "cap-gh-pr-create-invocation",
                    "subject_id": "gh-pr-create",
                    "capability_type": "invocation",
                    "claim_type": None,
                    "title": "gh pr create invocation",
                    "detail": {
                        "kind": "invocation",
                        "command": "gh pr create",
                        "argv_schema": {"program": "gh", "subcommand_path": ["pr", "create"]},
                    },
                    "source": "structured",
                    "verification_status": "accepted",
                    "verification_level": "L3_runtime_verified",
                    "confidence": 0.99,
                    "confidence_band": "strong",
                    "risk_level": "medium",
                    "evidence": [],
                    "relevance_reason": "title hit on ['create']",
                }
            ],
        }
    )
    assert response.capabilities[0].source == "structured"
    assert isinstance(response.capabilities[0].detail, dict)


def test_ayiru_error_format() -> None:
    err = AyiruError(
        status_code=404,
        code="CANONICAL_SPEC_NOT_FOUND",
        message="no spec for foo",
        details={"tool_id": "foo"},
    )
    s = str(err)
    assert "404" in s
    assert "CANONICAL_SPEC_NOT_FOUND" in s
    assert "no spec for foo" in s
    assert err.details == {"tool_id": "foo"}


def test_resolve_action_is_authoritative_property() -> None:
    response = ResolveActionResponse(
        subject_id="docker",
        action_intent="remove volume",
        resolution_mode="capability_search",
        fallback_recommended=False,
        verification_status="accepted",
        verification_level="L2_source_verified",
        confidence=0.8,
        confidence_band="high",
    )
    assert response.is_authoritative is True
