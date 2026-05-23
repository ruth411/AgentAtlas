"""Stage 19 — curated/uncurated split persistence contract.

Pins the new v0.2 default:
  - Unknown tool_ids (not in ayiru_stage_0.v2.json curated set) persist
    at L0 via ClaimStore.create() — no ToolNotAllowedError.
  - The strict v0.1 reject path is opt-in via AYIRU_STRICT_TOOL_LOCK=1.
  - The flag accepts the conventional truthy strings.

Counterpart tests for the matcher/ask surfaces live in
test_query_ask.py (test_ask_marks_uncurated_tools_in_match_reason,
test_ask_includes_pending_claims_for_curated_tools).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    RiskLevel,
    TrustLevel,
    VerificationStatus,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import (
    ClaimStore,
    ToolNotAllowedError,
    _curated_tool_ids,
    _strict_tool_lock_enabled,
)
from app.services.ids import generate_claim_id, generate_evidence_id
from tests.helpers import valid_sha256


FIXED = datetime(2026, 5, 23, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'uncurated.db'}")


def _evidence() -> Evidence:
    return Evidence(
        evidence_id=generate_evidence_id(),
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri="https://docs.example.com/some-tool",
        excerpt="example",
        hash=valid_sha256("ev"),
        captured_at=FIXED,
        trust_level=TrustLevel.HIGH,
    )


def _uncurated_claim() -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="kubectl scale",
        statement="scale a deployment to N replicas",
        tool_id="kubectl",  # NOT in the v2 curated contract
        submitted_by="test",
        evidence=[_evidence()],
        risk_level=RiskLevel.MEDIUM,
        created_at=FIXED,
    )


def test_uncurated_tool_is_not_in_curated_set() -> None:
    """Sanity check: kubectl is not in the v2 curated set. If this
    breaks, the v2 contract has drifted and the rest of the suite
    needs a different tool_id for uncurated tests."""
    assert "kubectl" not in _curated_tool_ids()


def test_unknown_tool_persists_at_l0_by_default(store: ClaimStore) -> None:
    """Stage 19 default: ClaimStore.create() accepts unknown tools and
    persists them. The claim lands at PENDING / L1_SCHEMA_VALID (the
    orchestrator's pre-verification state); the matcher's L2+ filter
    keeps it out of validate_command, but ask surfaces it with an
    uncurated marker."""
    claim = _uncurated_claim()
    persisted = store.create(claim)

    assert persisted.claim_id == claim.claim_id
    # Round-trip — the claim is now in the store.
    retrieved = store.get(claim.claim_id)
    assert retrieved is not None
    assert retrieved.tool_id == "kubectl"
    assert retrieved.verification_status == VerificationStatus.PENDING


def test_strict_tool_lock_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env var, strict mode is OFF — relaxed L0 path runs."""
    monkeypatch.delenv("AYIRU_STRICT_TOOL_LOCK", raising=False)
    assert _strict_tool_lock_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YES", "on", "On"])
def test_strict_tool_lock_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The conventional truthy strings all enable strict mode. Tested
    case-insensitive so operators don't trip on `TRUE` vs `true`."""
    monkeypatch.setenv("AYIRU_STRICT_TOOL_LOCK", value)
    assert _strict_tool_lock_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "anything-else"])
def test_strict_tool_lock_falsy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Anything other than the documented truthy strings is treated as
    OFF — including malformed input. We deliberately do NOT crash on
    garbage; an ops mis-config falls back to the (safer) relaxed default,
    not the (stricter) reject default. This is the right direction for
    a 'startup is trying to test, not block, contributions' mindset."""
    monkeypatch.setenv("AYIRU_STRICT_TOOL_LOCK", value)
    assert _strict_tool_lock_enabled() is False


def test_strict_mode_restores_v01_reject(
    store: ClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With AYIRU_STRICT_TOOL_LOCK=1, ClaimStore.create() should
    raise ToolNotAllowedError for any tool outside the curated set —
    the original v0.1 contract."""
    monkeypatch.setenv("AYIRU_STRICT_TOOL_LOCK", "1")

    with pytest.raises(ToolNotAllowedError) as exc:
        store.create(_uncurated_claim())

    assert exc.value.tool_id == "kubectl"
    # The error advertises the curated set so the operator knows which
    # tool_ids ARE allowed — preserves v0.1's debug ergonomics.
    assert "kubectl" not in exc.value.allowed


def test_curated_tool_still_works_in_strict_mode(
    store: ClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode must not break the v0.1 curated path. Claims for
    tools in the v2 contract's curated set still create successfully."""
    monkeypatch.setenv("AYIRU_STRICT_TOOL_LOCK", "1")

    curated_claim = KnowledgeClaim(
        claim_id=generate_claim_id(),
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="git status",
        statement="show the working tree status",
        tool_id="git",  # IS in v2 curated
        submitted_by="test",
        evidence=[_evidence()],
        risk_level=RiskLevel.LOW,
        created_at=FIXED,
    )
    persisted = store.create(curated_claim)
    assert persisted.tool_id == "git"
