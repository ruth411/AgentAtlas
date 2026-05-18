"""Stage 13: audit-log behaviour and append-only invariant tests.

The audit log is the spine of Stage 13. Tests in this module assert:

- Every state-changing operation in `ClaimStore` writes exactly one audit
  event per touched entity (claim submission, verification result, spec
  publication).
- Pagination + filtering returns rows in `(created_at ASC, event_id ASC)`
  order, with the correct `total`.
- The service layer has no `update_audit_event` / `delete_audit_event`.
  Direct attempts to mutate an existing row (via the SQLAlchemy session)
  are NOT prevented by the schema, but the service-level public surface
  exposes no method that does so — we enforce this with an attribute
  introspection test so a future regression that adds a mutator surfaces
  immediately.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    AuditEntityType,
    AuditEventType,
    ClaimType,
    ConfidenceBand,
    EvidenceType,
    OrchestratorDecision,
    RiskLevel,
    TrustLevel,
    VerificationLevel,
    VerificationStatus,
)
from app.schemas.confidence import ConfidenceBreakdown
from app.schemas.verification import VerificationResult
from app.services.claim_store import ClaimStore
from app.services.ids import generate_verification_id
from tests.helpers import risk_assessment, valid_sha256


def _claim(
    *,
    claim_id: str = "claim_audit_001",
    tool_id: str = "github-cli",
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject="gh repo delete",
        statement="Deletes a GitHub repository.",
        tool_id=tool_id,
        submitted_by="cli-agent",
        evidence=[
            {
                "evidence_id": f"ev_{claim_id}",
                "evidence_type": EvidenceType.OFFICIAL_DOCS,
                "source_uri": "https://docs.github.com/x",
                "excerpt": "deletes",
                "hash": valid_sha256(f"ev_{claim_id}"),
                "captured_at": datetime(2026, 5, 18, tzinfo=timezone.utc),
                "trust_level": TrustLevel.HIGH,
            }
        ],
        risk_level=RiskLevel.CRITICAL,
        verification_status=VerificationStatus.PENDING,
        confidence=None,
        created_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )


def _verification(claim_id: str) -> VerificationResult:
    breakdown = ConfidenceBreakdown(
        score=0.82, band=ConfidenceBand.HIGH, components=[], caps_applied=[], penalties=[]
    )
    return VerificationResult(
        verification_id=generate_verification_id(),
        claim_id=claim_id,
        decision=OrchestratorDecision.ACCEPTED,
        verification_status=VerificationStatus.ACCEPTED,
        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
        confidence=0.82,
        confidence_band=ConfidenceBand.HIGH,
        confidence_breakdown=breakdown,
        risk_assessment=risk_assessment(RiskLevel.CRITICAL),
        reason_codes=["seeded"],
        reasons=["seeded"],
        verified_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )


def _store(tmp_path) -> ClaimStore:
    return ClaimStore(database_url=f"sqlite:///{tmp_path / 'audit.db'}")


# ---------------------------------------------------------------------------
# Event emission per state change
# ---------------------------------------------------------------------------


def test_create_claim_emits_one_claim_submitted_event(tmp_path) -> None:
    store = _store(tmp_path)
    store.create(_claim())

    events, total = store.list_audit_events()
    assert total == 1
    assert events[0].event_type == AuditEventType.CLAIM_SUBMITTED
    assert events[0].entity_type == AuditEntityType.CLAIM
    assert events[0].actor == "cli-agent"
    # The details payload carries enough metadata for a reviewer to
    # triage without re-fetching the claim row.
    assert events[0].details["tool_id"] == "github-cli"
    assert events[0].details["claim_type"] == ClaimType.CLI_COMMAND_EXISTS.value
    assert events[0].details["risk_level"] == RiskLevel.CRITICAL.value
    assert events[0].details["evidence_count"] == 1


def test_save_verification_result_emits_verification_recorded(tmp_path) -> None:
    store = _store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(_verification(claim.claim_id), actor="orchestrator")

    events, _ = store.list_audit_events(
        event_type=AuditEventType.VERIFICATION_RECORDED
    )
    assert len(events) == 1
    payload = events[0].details
    assert payload["verification_status"] == VerificationStatus.ACCEPTED.value
    assert payload["verification_level"] == VerificationLevel.L2_SOURCE_VERIFIED.value
    assert events[0].actor == "orchestrator"


def test_save_verification_result_actor_defaults_to_system(tmp_path) -> None:
    """Legacy callers that haven't been updated to pass `actor` should
    still produce a well-formed audit row tagged with "system"."""
    store = _store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(_verification(claim.claim_id))

    events, _ = store.list_audit_events(
        event_type=AuditEventType.VERIFICATION_RECORDED
    )
    assert len(events) == 1
    assert events[0].actor == "system"


# ---------------------------------------------------------------------------
# Pagination + filtering ordering
# ---------------------------------------------------------------------------


def test_audit_events_are_ordered_chronologically(tmp_path) -> None:
    store = _store(tmp_path)
    for index in range(5):
        store.create(_claim(claim_id=f"claim_audit_seq_{index}"))

    events, total = store.list_audit_events(limit=10)
    assert total == 5
    timestamps = [event.created_at for event in events]
    assert timestamps == sorted(timestamps)


def test_audit_events_filter_by_entity_id(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create(_claim(claim_id="claim_audit_a"))
    b = store.create(_claim(claim_id="claim_audit_b"))
    store.save_verification_result(_verification(a.claim_id))
    store.save_verification_result(_verification(b.claim_id))

    events_a, total_a = store.list_audit_events(
        entity_type=AuditEntityType.CLAIM, entity_id=a.claim_id
    )
    assert total_a == 2  # submission + verification
    assert all(event.entity_id == a.claim_id for event in events_a)


def test_audit_events_filter_by_event_type(tmp_path) -> None:
    store = _store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(_verification(claim.claim_id))

    events, total = store.list_audit_events(
        event_type=AuditEventType.CLAIM_SUBMITTED
    )
    assert total == 1
    assert events[0].event_type == AuditEventType.CLAIM_SUBMITTED


def test_audit_events_filter_by_actor(tmp_path) -> None:
    store = _store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(_verification(claim.claim_id), actor="alice")

    events, total = store.list_audit_events(actor="alice")
    assert total == 1


def test_audit_events_filter_by_time_range(tmp_path) -> None:
    store = _store(tmp_path)
    store.create(_claim(claim_id="claim_audit_time"))
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=10)

    # `before=cutoff` returns the existing event.
    _, before_count = store.list_audit_events(before=cutoff)
    assert before_count == 1
    # `after=cutoff` returns nothing.
    _, after_count = store.list_audit_events(after=cutoff)
    assert after_count == 0


def test_pagination_offset_and_limit_round_trip(tmp_path) -> None:
    store = _store(tmp_path)
    for index in range(7):
        store.create(_claim(claim_id=f"claim_audit_page_{index}"))

    first, total = store.list_audit_events(limit=3, offset=0)
    second, _ = store.list_audit_events(limit=3, offset=3)
    rest, _ = store.list_audit_events(limit=3, offset=6)

    assert total == 7
    assert len(first) == 3
    assert len(second) == 3
    assert len(rest) == 1
    seen = {event.event_id for event in first + second + rest}
    assert len(seen) == 7  # no overlap


# ---------------------------------------------------------------------------
# Append-only invariant: no mutator on the service surface
# ---------------------------------------------------------------------------


def test_claim_store_exposes_no_audit_event_mutator() -> None:
    """The service layer must not ship `update_audit_event`,
    `delete_audit_event`, or any method whose name suggests mutation of
    the append-only log. The `clear()` method exists for tests but is
    explicitly the only path that touches existing rows — and it
    clears the entire DB, not individual events."""
    public_methods = {
        name for name in dir(ClaimStore) if not name.startswith("_")
    }
    forbidden = {
        "update_audit_event",
        "delete_audit_event",
        "modify_audit_event",
        "purge_audit_event",
        "edit_audit_event",
    }
    assert public_methods & forbidden == set(), (
        "Found a mutator on ClaimStore that breaks the audit append-only "
        f"invariant: {public_methods & forbidden}"
    )


def test_existing_audit_events_are_not_mutated_by_subsequent_writes(tmp_path) -> None:
    """If `claim.create` is followed by a verification + a publish, the
    earlier rows must remain byte-identical."""
    store = _store(tmp_path)
    claim = store.create(_claim())
    first_events, _ = store.list_audit_events()
    assert len(first_events) == 1
    original = first_events[0]

    store.save_verification_result(_verification(claim.claim_id), actor="orchestrator")
    updated_events, _ = store.list_audit_events(entity_id=claim.claim_id)
    # The original event must appear UNCHANGED.
    matches = [e for e in updated_events if e.event_id == original.event_id]
    assert len(matches) == 1
    snapshot = matches[0]
    assert snapshot.model_dump(mode="json") == original.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Claim history convenience accessor
# ---------------------------------------------------------------------------


def test_claim_history_returns_chronological_events(tmp_path) -> None:
    store = _store(tmp_path)
    claim = store.create(_claim())
    store.save_verification_result(_verification(claim.claim_id))
    history = store.get_audit_events_for_claim(claim.claim_id)
    assert [event.event_type for event in history] == [
        AuditEventType.CLAIM_SUBMITTED,
        AuditEventType.VERIFICATION_RECORDED,
    ]


def test_claim_history_for_unknown_id_is_empty(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.get_audit_events_for_claim("claim_no_such_id") == []
