"""Regression tests for the deep-sweep cracks closed after the Stage 7a verdict.

Each test pins one crack so future refactors cannot silently reintroduce it.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.claim import ClaimCreate, KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    IngestionStatus,
    RiskLevel,
    TrustLevel,
    VerificationStatus,
)
from app.services.claim_store import (
    ClaimStore,
    ToolNotAllowedError,
    get_claim_store,
)
from app.services.evidence_policy import _is_rejected_source
from tests.helpers import valid_sha256


FIXED_TIME = datetime(2026, 5, 14, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'ayiru.db'}")


@pytest.fixture
def client(store: ClaimStore):
    app.dependency_overrides[get_claim_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _evidence(evidence_id: str = "ev_test", source_uri: str = "https://docs.github.com/cli/manual/gh_repo_delete") -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": EvidenceType.OFFICIAL_DOCS,
        "source_uri": source_uri,
        "excerpt": "x",
        "hash": valid_sha256(evidence_id),
        "captured_at": FIXED_TIME,
        "trust_level": TrustLevel.HIGH,
    }


def _knowledge_claim(**overrides) -> dict:
    base = dict(
        claim_id="claim_ok",
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="gh repo delete",
        statement="Delete a repo.",
        tool_id="github-cli",
        submitted_by="anon",
        evidence=[_evidence()],
        risk_level=RiskLevel.LOW,
        verification_status=VerificationStatus.PENDING,
        confidence=None,
        created_at=FIXED_TIME,
    )
    base.update(overrides)
    return base


# ============================================================================
# CRACK 3 — Positive URL-scheme allowlist
# ============================================================================


@pytest.mark.parametrize(
    "source_uri",
    [
        "javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "vbscript:msgbox('x')",
        "ftp://bad/p",
        "chrome-extension://abc",
        "llm://memory",
        "memory://recall",
        "sandbox://faked",
        "maintainer-review://unverified",
        "noscheme",
        "",
    ],
)
def test_dangerous_or_unknown_url_schemes_are_rejected(source_uri: str) -> None:
    assert _is_rejected_source(source_uri) is True


@pytest.mark.parametrize(
    "source_uri",
    [
        "https://docs.github.com/cli/manual/gh_repo_delete",
        "http://example.com/path",
        "ayiru://ingestion/run_x/artifacts/a_y",
        "local://help.txt",
        "file:///var/log/x.txt",
        "docs://vercel-cli/deploy",
    ],
)
def test_allowed_schemes_are_accepted(source_uri: str) -> None:
    assert _is_rejected_source(source_uri) is False


def test_https_with_rejected_host_label_is_still_rejected() -> None:
    assert _is_rejected_source("https://blog.example.com/post") is True
    assert _is_rejected_source("https://medium.com/x") is True
    assert _is_rejected_source("https://stackoverflow.com/q/1") is True


# ============================================================================
# CRACK 8 — Unbounded statement length
# ============================================================================


def test_statement_over_4000_chars_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(**_knowledge_claim(statement="x" * 5000))
    with pytest.raises(ValidationError):
        ClaimCreate(
            claim_id="claim_long",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="x",
            statement="x" * 5000,
            tool_id="git",
            submitted_by="x",
            evidence=[_evidence()],
            risk_level=RiskLevel.LOW,
        )


# ============================================================================
# CRACK 11 — Length caps on subject, tool_id, submitted_by, claim_id
# ============================================================================


def test_subject_over_512_chars_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(**_knowledge_claim(subject="x" * 600))


def test_tool_id_over_128_chars_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(**_knowledge_claim(tool_id="x" * 200))


def test_submitted_by_over_128_chars_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(**_knowledge_claim(submitted_by="x" * 200))


def test_claim_id_over_128_chars_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(**_knowledge_claim(claim_id="x" * 200))


# ============================================================================
# CRACK 21/22 — Evidence list cap
# ============================================================================


def test_evidence_list_over_50_records_is_rejected() -> None:
    too_many = [_evidence(f"ev_{i}") for i in range(60)]
    with pytest.raises(ValidationError):
        KnowledgeClaim(**_knowledge_claim(evidence=too_many))


# ============================================================================
# CRACK 7 — Identifier pattern validators (no path traversal etc.)
# ============================================================================


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "claim id with spaces",
        "claim/with/slashes",
        "claim;DROP TABLE x",
        "claim<script>",
        "",
        "x" * 200,
    ],
)
def test_identifier_pattern_rejects_bad_ids(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        ClaimCreate(
            claim_id=bad_id,
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject="x",
            statement="x",
            tool_id="git",
            submitted_by="x",
            evidence=[_evidence()],
            risk_level=RiskLevel.LOW,
        )


@pytest.mark.parametrize("good_id", ["claim_123", "ver_abc.def", "ev-1", "claim_X.Y-Z"])
def test_identifier_pattern_accepts_safe_ids(good_id: str) -> None:
    payload = ClaimCreate(
        claim_id=good_id,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="x",
        statement="x",
        tool_id="git",
        submitted_by="x",
        evidence=[_evidence(evidence_id=good_id)],
        risk_level=RiskLevel.LOW,
    )
    assert payload.claim_id == good_id


# ============================================================================
# CRACK 4 — workflow_step subject pattern enforced at submit
# ============================================================================


def test_workflow_step_with_invalid_subject_is_rejected_at_submit() -> None:
    with pytest.raises(ValidationError):
        KnowledgeClaim(**_knowledge_claim(
            claim_id="wf_bad",
            claim_type=ClaimType.WORKFLOW_STEP,
            subject="not-a-workflow-subject",
            tool_id="git",
        ))


def test_workflow_step_with_valid_subject_is_accepted() -> None:
    claim = KnowledgeClaim(**_knowledge_claim(
        claim_id="wf_good",
        claim_type=ClaimType.WORKFLOW_STEP,
        subject="deploy-next::1::check_status",
        tool_id="git",
    ))
    assert claim.claim_type == ClaimType.WORKFLOW_STEP


# ============================================================================
# CRACK 2 — Stage 0 tool_id gate
# ============================================================================


def test_garbage_tool_id_is_rejected_by_store(store: ClaimStore) -> None:
    garbage = KnowledgeClaim(**_knowledge_claim(
        claim_id="garbage",
        tool_id="nonexistent-tool",
    ))
    with pytest.raises(ToolNotAllowedError):
        store.create(garbage)


def test_garbage_tool_id_returns_structured_422_via_route(client) -> None:
    response = client.post(
        "/claims",
        json={
            "claim_id": "claim_garb",
            "claim_type": "cli_command_exists",
            "subject": "frob",
            "statement": "frob",
            "tool_id": "definitely-not-a-stage-0-tool",
            "submitted_by": "anon",
            "evidence": [
                {
                    "evidence_id": "ev_g",
                    "evidence_type": "official_docs",
                    "source_uri": "https://docs.example.com/x",
                    "excerpt": "frob",
                    "hash": valid_sha256("ev_g"),
                    "captured_at": FIXED_TIME.isoformat(),
                    "trust_level": "high",
                }
            ],
            "risk_level": "low",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TOOL_NOT_ALLOWED"


# ============================================================================
# CRACK 5 — Pre-check evidence dupe (no fragile substring match)
# ============================================================================


def test_evidence_dupe_caught_by_pre_check_not_error_string(store: ClaimStore) -> None:
    """Two claims that share an evidence_id must surface DuplicateEvidenceError
    via the pre-check path; we deliberately do not exercise the IntegrityError
    fallback here."""
    from app.services.claim_store import DuplicateEvidenceError

    first = KnowledgeClaim(**_knowledge_claim(claim_id="c1", evidence=[_evidence("shared_ev")]))
    second = KnowledgeClaim(**_knowledge_claim(claim_id="c2", evidence=[_evidence("shared_ev")]))
    store.create(first)
    with pytest.raises(DuplicateEvidenceError):
        store.create(second)


# ============================================================================
# CRACK 13 — Pagination
# ============================================================================


def test_list_claims_respects_limit_and_offset(client, store: ClaimStore) -> None:
    for i in range(5):
        store.create(KnowledgeClaim(**_knowledge_claim(
            claim_id=f"claim_{i}",
            evidence=[_evidence(f"ev_paged_{i}")],
        )))

    page1 = client.get("/claims", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/claims", params={"limit": 2, "offset": 2}).json()
    page3 = client.get("/claims", params={"limit": 2, "offset": 4}).json()
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    seen = {c["claim_id"] for c in page1 + page2 + page3}
    assert seen == {f"claim_{i}" for i in range(5)}


def test_list_claims_rejects_limit_zero(client) -> None:
    response = client.get("/claims", params={"limit": 0})
    assert response.status_code == 422


def test_list_claims_rejects_limit_above_max(client) -> None:
    response = client.get("/claims", params={"limit": 1000})
    assert response.status_code == 422


# ============================================================================
# CRACK 27 — Request size middleware
# ============================================================================


def test_oversized_content_length_is_rejected_with_413(client) -> None:
    headers = {"content-length": str(2 * 1024 * 1024)}
    response = client.post("/claims", content=b"{}", headers=headers)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


def test_normal_sized_request_passes_middleware(client) -> None:
    response = client.post(
        "/claims",
        json={
            "claim_id": "claim_size",
            "claim_type": "cli_command_exists",
            "subject": "git status",
            "statement": "git status reads working tree.",
            "tool_id": "git",
            "submitted_by": "anon",
            "evidence": [
                {
                    "evidence_id": "ev_size",
                    "evidence_type": "official_docs",
                    "source_uri": "https://git-scm.com/docs/git-status",
                    "excerpt": "x",
                    "hash": valid_sha256("ev_size"),
                    "captured_at": FIXED_TIME.isoformat(),
                    "trust_level": "high",
                }
            ],
            "risk_level": "low",
        },
    )
    assert response.status_code == 201


# ============================================================================
# CRACK 20 — IngestionStatus.IN_PROGRESS
# ============================================================================


def test_ingestion_status_in_progress_exists() -> None:
    assert IngestionStatus.IN_PROGRESS.value == "in_progress"


def test_initial_ingestion_run_persists_as_in_progress(store: ClaimStore) -> None:
    """While ingestion is mid-flight, the persisted run status reflects
    IN_PROGRESS (not the misleading FAILED) until the final transition."""
    from app.schemas.ingestion import CliIngestionSummary, IngestionRun

    run = IngestionRun(
        run_id="run_inprog",
        tool_id="git",
        command="git status",
        status=IngestionStatus.IN_PROGRESS,
        submitted_by="cli-ingestion-agent",
        created_at=FIXED_TIME,
        summary=CliIngestionSummary(capture_argv=["git", "status", "-h"]),
    )
    store.save_ingestion_run(run)
    fetched = store.get_ingestion_run("run_inprog")
    assert fetched is not None
    assert fetched.status == IngestionStatus.IN_PROGRESS


# ============================================================================
# CRACK 15 — Canonical save IntegrityError handled (retry succeeds)
# ============================================================================


def test_canonical_save_retries_on_integrity_error(store: ClaimStore, monkeypatch) -> None:
    """Simulate a TOCTOU race by raising IntegrityError on first commit and
    confirming the retry path completes successfully."""
    from datetime import datetime as _dt

    from sqlalchemy.exc import IntegrityError

    from app.schemas.tool_spec import (
        AuthRequirement,
        Provenance,
        RiskProfile,
        ToolSpec,
    )
    from app.schemas.enums import VerificationLevel

    spec = ToolSpec(
        tool_id="git",
        name="Git",
        interfaces=["cli"],
        capabilities=["x"],
        auth=AuthRequirement(required=None, methods=[]),
        risk_profile=RiskProfile(default_risk_level=RiskLevel.LOW),
        provenance=Provenance(
            source_claim_ids=["c"],
            source_evidence_ids=["e"],
            compiled_at=_dt(2026, 5, 16, tzinfo=timezone.utc),
            compiled_by="t",
            verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        ),
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
    )

    call_counter = {"n": 0}
    original_commit = None  # type: ignore[var-annotated]

    def patched_commit(self):  # type: ignore[no-redef]
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            raise IntegrityError("simulated", None, Exception("conflict"))
        original_commit(self)

    from sqlalchemy.orm import Session as _Session

    original_commit = _Session.commit  # type: ignore[assignment]
    monkeypatch.setattr(_Session, "commit", patched_commit)

    result = store.save_canonical_tool_spec(spec)
    assert result.tool_id == "git"
    # First call raised, second call succeeded — confirms retry path.
    assert call_counter["n"] >= 2
