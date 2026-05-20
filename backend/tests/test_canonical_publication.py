from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.enums import (
    ClaimType,
    ConfidenceBand,
    EvidenceType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.risk import RiskAssessment, RiskDimension
from app.schemas.verification import VerificationResult
from app.services.canonical_compiler import (
    CanonicalPublicationError,
    ToolSpecCompiler,
    WorkflowSpecCompiler,
)
from app.services.claim_store import (
    ClaimStore,
    canonical_content_hash,
    canonical_spec_hash,
    get_claim_store,
)
from tests.helpers import valid_sha256


FIXED_TIME = datetime(2026, 5, 16, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


@pytest.fixture
def client(store: ClaimStore):
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _breakdown(score: float = 0.85, band: ConfidenceBand = ConfidenceBand.HIGH) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(score=score, band=band, components=[], caps_applied=[], penalties=[])


def _risk(
    risk_level: RiskLevel,
    dimensions: list[RiskDimension] | None = None,
) -> RiskAssessment:
    return RiskAssessment(
        risk_level=risk_level,
        reasons=["test risk"],
        requires_confirmation=risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL},
        safe_to_auto_execute=risk_level in {RiskLevel.NONE, RiskLevel.LOW},
        dimensions=dimensions or [],
        matched_rule_ids=["test.rule"],
        matches=[],
    )


def _claim(
    *,
    claim_id: str,
    claim_type: ClaimType,
    subject: str,
    statement: str,
    tool_id: str = "github-cli",
    risk_level: RiskLevel = RiskLevel.LOW,
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=claim_type,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        submitted_by="test-agent",
        evidence=[
            {
                "evidence_id": f"ev_{claim_id}",
                "evidence_type": EvidenceType.OFFICIAL_DOCS,
                "source_uri": "https://docs.github.com/cli/manual/gh_repo_delete",
                "excerpt": "Evidence excerpt.",
                "hash": valid_sha256(f"ev_{claim_id}"),
                "captured_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "trust_level": TrustLevel.HIGH,
            }
        ],
        risk_level=risk_level,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )


def _accept(
    store: ClaimStore,
    claim: KnowledgeClaim,
    *,
    risk: RiskAssessment | None = None,
    level: VerificationLevel = VerificationLevel.L2_SOURCE_VERIFIED,
) -> None:
    store.create(claim)
    store.save_verification_result(
        VerificationResult(
            verification_id=f"ver_{claim.claim_id}",
            claim_id=claim.claim_id,
            decision=OrchestratorDecision.ACCEPTED,
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=level,
            confidence=0.85,
            confidence_band=ConfidenceBand.HIGH,
            confidence_breakdown=_breakdown(),
            risk_assessment=risk or _risk(claim.risk_level),
            reason_codes=["TRUSTED_EVIDENCE_NO_CONFLICTS"],
            reasons=["Accepted for canonical publication test."],
            verified_at=FIXED_TIME,
        )
    )


def test_tool_compiler_uses_only_accepted_claims_and_preserves_provenance(store: ClaimStore) -> None:
    _accept(
        store,
        _claim(
            claim_id="claim_command",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="Delete a GitHub repository.",
            risk_level=RiskLevel.CRITICAL,
        ),
        risk=_risk(
            RiskLevel.CRITICAL,
            [RiskDimension.DESTRUCTIVE, RiskDimension.REMOTE_MUTATION],
        ),
    )
    _accept(
        store,
        _claim(
            claim_id="claim_auth",
            claim_type=ClaimType.AUTH_REQUIREMENT,
            subject="GitHub CLI authentication",
            statement="GitHub CLI requires authentication for repository deletion.",
        ),
    )
    store.create(
        _claim(
            claim_id="claim_pending",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo create",
            statement="Pending claim must not publish.",
        )
    )

    compiled = ToolSpecCompiler(store).compile("github-cli", compiled_at=FIXED_TIME)
    spec = compiled.spec

    assert spec.tool_id == "github-cli"
    assert [command.name for command in spec.commands] == ["gh repo delete"]
    assert spec.auth.required is None
    assert spec.provenance.source_claim_ids == ["claim_auth", "claim_command"]
    assert spec.provenance.source_evidence_ids == ["ev_claim_auth", "ev_claim_command"]
    assert spec.verification_level == VerificationLevel.L2_SOURCE_VERIFIED
    assert spec.risk_profile.can_delete_resources is True
    assert spec.risk_profile.mutates_remote_state is True
    assert spec.content_hash == canonical_content_hash(spec)
    assert compiled.spec_hash == canonical_spec_hash(spec)
    assert "unstructured_auth_claim" in {issue.code for issue in spec.publication_issues}


def test_tool_compiler_emits_missing_auth_issue_without_inventing_auth(store: ClaimStore) -> None:
    _accept(
        store,
        _claim(
            claim_id="claim_command",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="Delete a GitHub repository.",
        ),
    )

    spec = ToolSpecCompiler(store).compile("github-cli", compiled_at=FIXED_TIME).spec

    assert spec.auth.required is None
    assert "missing_auth_claim" in {issue.code for issue in spec.publication_issues}
    assert "missing_command_side_effects" in {issue.code for issue in spec.publication_issues}


def test_tool_compiler_is_deterministic_for_same_inputs_and_compiled_at(store: ClaimStore) -> None:
    _accept(
        store,
        _claim(
            claim_id="claim_command",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="Delete a GitHub repository.",
        ),
    )

    first = ToolSpecCompiler(store).compile("github-cli", compiled_at=FIXED_TIME)
    second = ToolSpecCompiler(store).compile("github-cli", compiled_at=FIXED_TIME)

    assert first.spec_json == second.spec_json
    assert first.spec_hash == second.spec_hash
    assert first.content_hash == second.content_hash


def test_content_hash_ignores_publication_timestamp(store: ClaimStore) -> None:
    _accept(
        store,
        _claim(
            claim_id="claim_command",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="Delete a GitHub repository.",
        ),
    )

    first = ToolSpecCompiler(store).compile("github-cli", compiled_at=FIXED_TIME)
    second = ToolSpecCompiler(
        store,
    ).compile("github-cli", compiled_at=datetime(2026, 5, 17, tzinfo=timezone.utc))

    assert first.spec_hash != second.spec_hash
    assert first.content_hash == second.content_hash


def test_tool_compiler_refuses_publication_without_accepted_claims(store: ClaimStore) -> None:
    with pytest.raises(CanonicalPublicationError):
        ToolSpecCompiler(store).compile("github-cli", compiled_at=FIXED_TIME)


def test_store_persists_and_replaces_canonical_tool_spec(store: ClaimStore) -> None:
    _accept(
        store,
        _claim(
            claim_id="claim_command",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="Delete a GitHub repository.",
        ),
    )
    compiler = ToolSpecCompiler(store)
    first = compiler.compile("github-cli", compiled_at=FIXED_TIME).spec
    store.save_canonical_tool_spec(first)
    second = compiler.compile(
        "github-cli",
        compiled_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
    ).spec
    store.save_canonical_tool_spec(second)

    assert store.get_canonical_tool_spec("github-cli") == second
    assert [spec.tool_id for spec in store.list_canonical_tool_specs()] == ["github-cli"]


def test_workflow_compiler_orders_steps_and_reports_malformed_subject(store: ClaimStore) -> None:
    _accept(
        store,
        _claim(
            claim_id="claim_step_2",
            claim_type=ClaimType.WORKFLOW_STEP,
            subject="deploy-next::2::deploy_preview",
            statement="vercel",
            tool_id="vercel-cli",
        ),
    )
    _accept(
        store,
        _claim(
            claim_id="claim_step_1",
            claim_type=ClaimType.WORKFLOW_STEP,
            subject="deploy-next::1::check_status",
            statement="git status",
            tool_id="git",
        ),
    )
    # Step number 0 passes the schema-level regex (workflow_id::N::action)
    # but the canonical compiler rejects it for step_number < 1, surfacing the
    # invalid_workflow_subject issue. The schema-level regex now blocks the
    # original "deploy-next::bad::broken" form entirely.
    _accept(
        store,
        _claim(
            claim_id="claim_bad",
            claim_type=ClaimType.WORKFLOW_STEP,
            subject="deploy-next::0::broken",
            statement="bad",
            tool_id="git",
        ),
    )

    spec = WorkflowSpecCompiler(store).compile("deploy-next", compiled_at=FIXED_TIME).spec

    assert [step.step_id for step in spec.steps] == ["step_1", "step_2"]
    assert [step.action for step in spec.steps] == ["check_status", "deploy_preview"]
    assert [step.command for step in spec.steps] == [None, None]
    assert [step.description for step in spec.steps] == ["git status", "vercel"]
    assert spec.goal is None
    assert spec.tool_ids == ["git", "vercel-cli"]
    assert spec.provenance.source_claim_ids == ["claim_bad", "claim_step_1", "claim_step_2"]
    assert {issue.code for issue in spec.publication_issues} == {
        "invalid_workflow_subject",
        "missing_workflow_goal",
        "missing_workflow_step_command",
    }


def test_canonical_api_publishes_retrieves_and_lists_specs(
    client: TestClient, store: ClaimStore
) -> None:
    _accept(
        store,
        _claim(
            claim_id="claim_command",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="gh repo delete",
            statement="Delete a GitHub repository.",
        ),
    )

    published = client.post("/canonical/tools/github-cli/publish")
    fetched = client.get("/canonical/tools/github-cli")
    listed = client.get("/canonical/tools")

    assert published.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json() == published.json()
    assert [item["tool_id"] for item in listed.json()] == ["github-cli"]


def test_canonical_api_returns_structured_errors(client: TestClient) -> None:
    missing = client.get("/canonical/tools/github-cli")
    publish = client.post("/canonical/tools/github-cli/publish")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CANONICAL_SPEC_NOT_FOUND"
    assert publish.status_code == 422
    assert publish.json()["error"]["code"] == "CANONICAL_PUBLICATION_FAILED"
