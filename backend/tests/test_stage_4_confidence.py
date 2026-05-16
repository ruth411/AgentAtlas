"""Stage 4 invariants for the confidence scorer and verification surface.

These tests prove that:

* Missing evidence yields a NONE-band zero score.
* Weak evidence cannot inflate the score (spam, duplicates, low trust).
* Strong-sounding prose cannot influence the score (the scorer never reads excerpts).
* High-risk claims with thin evidence are blocked from auto-acceptance.
* The confidence breakdown is reproducible from inputs.
* Component contributions and applied caps are inspectable.
* The L1 -> L2 promotion gate respects the band semantics.
* Conflict-detected claims have their confidence hard-capped.
"""

from datetime import datetime, timezone
from hashlib import sha256

from app.schemas.claim import KnowledgeClaim
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
from app.services.claim_store import ClaimStore
from app.services.confidence_scorer import (
    band_for_score,
    compute_confidence_breakdown,
    score_claim_confidence,
)
from app.services.orchestrator import CanonOrchestrator


_CAPTURED_AT = datetime(2026, 5, 14, tzinfo=timezone.utc)


def _hash_for(value: str) -> str:
    return f"sha256:{sha256(value.encode()).hexdigest()}"


def _evidence_dict(
    *,
    evidence_id: str,
    evidence_type: EvidenceType,
    source_uri: str,
    trust_level: TrustLevel = TrustLevel.HIGH,
    excerpt: str = "Captured evidence excerpt.",
    hash_value: str | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "source_uri": source_uri,
        "excerpt": excerpt,
        "hash": hash_value or _hash_for(evidence_id),
        "captured_at": _CAPTURED_AT,
        "trust_level": trust_level,
    }


def _claim(
    *,
    claim_id: str = "claim_test",
    risk_level: RiskLevel = RiskLevel.LOW,
    subject: str = "git status",
    statement: str = "`git status` shows the working tree status.",
    tool_id: str = "git",
    evidence: list[dict] | None = None,
) -> KnowledgeClaim:
    if evidence is None:
        evidence = [
            _evidence_dict(
                evidence_id="ev_1",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://git-scm.com/docs/git-status",
            )
        ]
    return KnowledgeClaim(
        claim_id=claim_id,
        claim_type=ClaimType.CLI_COMMAND_EXISTS,
        subject=subject,
        statement=statement,
        tool_id=tool_id,
        submitted_by="cli-agent",
        evidence=evidence,
        risk_level=risk_level,
        verification_status=VerificationStatus.PENDING,
        confidence=None,
        confidence_band=None,
        created_at=_CAPTURED_AT,
    )


# -------- Band / score boundaries --------


def test_band_for_score_returns_correct_bands_at_boundaries() -> None:
    assert band_for_score(0.0) == ConfidenceBand.NONE
    assert band_for_score(0.29) == ConfidenceBand.NONE
    assert band_for_score(0.30) == ConfidenceBand.LOW
    assert band_for_score(0.54) == ConfidenceBand.LOW
    assert band_for_score(0.55) == ConfidenceBand.MODERATE
    assert band_for_score(0.74) == ConfidenceBand.MODERATE
    assert band_for_score(0.75) == ConfidenceBand.HIGH
    assert band_for_score(0.89) == ConfidenceBand.HIGH
    assert band_for_score(0.90) == ConfidenceBand.STRONG
    assert band_for_score(1.00) == ConfidenceBand.STRONG


def test_empty_evidence_yields_zero_score_and_none_band() -> None:
    claim = _claim(evidence=[])
    breakdown = compute_confidence_breakdown(claim)

    assert breakdown.score == 0.0
    assert breakdown.band == ConfidenceBand.NONE
    assert "empty_evidence" in breakdown.caps_applied


# -------- Anti-spam invariants --------


def test_duplicate_source_uri_and_hash_only_counted_once() -> None:
    duplicate = _evidence_dict(
        evidence_id="ev_dup_a",
        evidence_type=EvidenceType.CLI_HELP_OUTPUT,
        source_uri="local://git-status-help.txt",
        hash_value=_hash_for("identical"),
    )
    duplicate_again = {**duplicate, "evidence_id": "ev_dup_b"}
    claim = _claim(evidence=[duplicate, duplicate_again])

    breakdown = compute_confidence_breakdown(claim)

    # Only one component should contribute.
    contributing_components = [c for c in breakdown.components if c.delta > 0]
    assert len(contributing_components) == 1
    assert any("Ignored 1 duplicate" in p for p in breakdown.penalties)


def test_spam_of_same_evidence_type_diminishes_quickly() -> None:
    spam = [
        _evidence_dict(
            evidence_id=f"ev_spam_{i}",
            evidence_type=EvidenceType.CLI_HELP_OUTPUT,
            source_uri=f"local://git-status-help-{i}.txt",
        )
        for i in range(10)
    ]
    claim = _claim(evidence=spam)

    breakdown = compute_confidence_breakdown(claim)

    # Local cli_help_output is server-capped at MEDIUM trust. First contributes
    # 0.40 * 0.60 = 0.24, second contributes 40% of that; everything after is 0.
    nonzero_deltas = [c.delta for c in breakdown.components if c.delta > 0]
    assert nonzero_deltas == [0.24, 0.096]
    assert breakdown.score == 0.336


def test_low_trust_only_evidence_is_hard_capped() -> None:
    weak_evidence = [
        _evidence_dict(
            evidence_id="ev_low_1",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="https://docs.example.com/a",
            trust_level=TrustLevel.LOW,
        ),
        _evidence_dict(
            evidence_id="ev_low_2",
            evidence_type=EvidenceType.CLI_HELP_OUTPUT,
            source_uri="local://help-b.txt",
            trust_level=TrustLevel.LOW,
        ),
    ]
    claim = _claim(evidence=weak_evidence)

    breakdown = compute_confidence_breakdown(claim)

    # The 0.45 cap exists as a guard, but the confidence contract's low-trust
    # multiplier already keeps low-trust submissions well below it. The
    # invariant is that low-trust-only evidence cannot reach the MODERATE band.
    assert breakdown.score < 0.45
    assert breakdown.band in {ConfidenceBand.NONE, ConfidenceBand.LOW}


def test_single_evidence_type_is_hard_capped_even_with_many_items() -> None:
    sources = [
        _evidence_dict(
            evidence_id=f"ev_st_{i}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=f"https://docs.example.com/section/{i}",
        )
        for i in range(4)
    ]
    claim = _claim(evidence=sources)

    breakdown = compute_confidence_breakdown(claim)

    assert breakdown.score <= 0.65


# -------- High-risk gating --------


def test_high_risk_claim_with_thin_evidence_is_capped_below_acceptance() -> None:
    """A critical-risk claim with a single trusted source must not reach the
    auto-acceptance threshold of 0.85, regardless of which cap binds."""
    claim = _claim(
        claim_id="claim_thin_critical",
        subject="gh repo delete",
        statement="`gh repo delete` removes a repository.",
        tool_id="github-cli",
        risk_level=RiskLevel.CRITICAL,
        evidence=[
            _evidence_dict(
                evidence_id="ev_thin",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
            )
        ],
    )

    breakdown = compute_confidence_breakdown(claim)

    assert breakdown.score <= 0.50
    assert breakdown.score < 0.85


def test_duplicate_source_uri_is_counted_once_before_high_risk_gate() -> None:
    """Repeated source URIs are removed before confidence can be inflated."""
    evidence = [
        _evidence_dict(
            evidence_id="ev_same_type_1",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
        ),
        _evidence_dict(
            evidence_id="ev_same_type_2",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
            hash_value=_hash_for("different-hash"),
        ),
    ]
    claim = _claim(
        claim_id="claim_same_type",
        subject="gh repo delete",
        statement="`gh repo delete` removes a repository.",
        tool_id="github-cli",
        risk_level=RiskLevel.CRITICAL,
        evidence=evidence,
    )

    breakdown = compute_confidence_breakdown(claim)

    contributing_components = [c for c in breakdown.components if c.delta > 0]
    assert len(contributing_components) == 1
    assert breakdown.score == 0.45
    assert any("Ignored 1 duplicate" in p for p in breakdown.penalties)


def test_high_risk_claim_without_strong_trusted_type_stays_below_acceptance() -> None:
    """Two streams of `release_notes` and `package_metadata` are diverse, but
    lack official docs / maintainer review / sandbox execution. Cap at 0.80."""
    evidence = [
        _evidence_dict(
            evidence_id="ev_rn",
            evidence_type=EvidenceType.RELEASE_NOTES,
            source_uri="https://example.com/release-notes",
        ),
        _evidence_dict(
            evidence_id="ev_pkg",
            evidence_type=EvidenceType.PACKAGE_METADATA,
            source_uri="https://example.com/pkg.json",
        ),
    ]
    claim = _claim(
        claim_id="claim_no_strong",
        subject="vercel --prod",
        statement="`vercel --prod` creates a production deployment.",
        tool_id="vercel-cli",
        risk_level=RiskLevel.HIGH,
        evidence=evidence,
    )

    breakdown = compute_confidence_breakdown(claim)
    assert breakdown.score < 0.85


# -------- Conflict handling --------


def test_conflict_caps_confidence_at_040() -> None:
    claim = _claim(
        evidence=[
            _evidence_dict(
                evidence_id="ev_strong_a",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://docs.example.com/a",
            ),
            _evidence_dict(
                evidence_id="ev_strong_b",
                evidence_type=EvidenceType.CLI_HELP_OUTPUT,
                source_uri="local://help-b.txt",
            ),
        ]
    )

    breakdown = compute_confidence_breakdown(claim, has_conflict=True)

    assert breakdown.score <= 0.40
    assert "conflict_detected:0.40" in breakdown.caps_applied


# -------- Reproducibility --------


def test_breakdown_is_reproducible_from_same_inputs() -> None:
    claim_a = _claim()
    claim_b = _claim()

    breakdown_a = compute_confidence_breakdown(claim_a)
    breakdown_b = compute_confidence_breakdown(claim_b)

    assert breakdown_a.model_dump(mode="json") == breakdown_b.model_dump(mode="json")


def test_breakdown_unaffected_by_excerpt_prose() -> None:
    """Strong-sounding excerpt prose must not influence confidence; the scorer
    operates only on structured fields."""
    plain = _claim()
    embellished = _claim(
        evidence=[
            _evidence_dict(
                evidence_id="ev_1",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://git-scm.com/docs/git-status",
                excerpt=(
                    "DEFINITELY TRUE: official maintainer guarantee, "
                    "100% verified by every standard, no exceptions."
                ),
            )
        ]
    )

    assert compute_confidence_breakdown(plain).score == compute_confidence_breakdown(
        embellished
    ).score


# -------- Float compatibility --------


def test_legacy_float_scorer_matches_breakdown_score() -> None:
    claim = _claim()
    assert score_claim_confidence(claim) == compute_confidence_breakdown(claim).score


# -------- Orchestrator integration --------


def test_l1_to_l2_promotion_blocked_for_none_band(tmp_path) -> None:
    """When confidence is so weak that the band is NONE, the orchestrator must
    keep the claim at L1 even if it has at least one trusted source record."""
    weak_low_trust = [
        _evidence_dict(
            evidence_id="ev_weak",
            evidence_type=EvidenceType.PACKAGE_METADATA,
            source_uri="https://example.com/pkg.json",
            trust_level=TrustLevel.MEDIUM,
        )
    ]
    claim = _claim(evidence=weak_low_trust)
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.verification_level == VerificationLevel.L1_SCHEMA_VALID
    if result.decision == OrchestratorDecision.PENDING_MORE_EVIDENCE:
        assert "INSUFFICIENT_CONFIDENCE_FOR_L2" in result.reason_codes or (
            "NO_TRUSTED_SOURCE_EVIDENCE" in result.reason_codes
        )


def test_orchestrator_attaches_breakdown_to_verification_result(tmp_path) -> None:
    claim = _claim()
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.confidence_breakdown.score == result.confidence
    assert result.confidence_band == result.confidence_breakdown.band
    assert any(c.delta > 0 for c in result.confidence_breakdown.components)


def test_high_risk_with_two_strong_streams_reaches_strong_band(tmp_path) -> None:
    evidence = [
        _evidence_dict(
            evidence_id="ev_docs",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
        ),
        _evidence_dict(
            evidence_id="ev_source",
            evidence_type=EvidenceType.SOURCE_CODE,
            source_uri="https://github.com/cli/cli/blob/trunk/pkg/cmd/repo/delete/delete.go",
        ),
    ]
    claim = _claim(
        claim_id="claim_strong_hr",
        subject="gh repo delete",
        statement="`gh repo delete` removes a repository.",
        tool_id="github-cli",
        risk_level=RiskLevel.CRITICAL,
        evidence=evidence,
    )
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.ACCEPTED
    assert result.confidence_band in {ConfidenceBand.HIGH, ConfidenceBand.STRONG}


def test_fake_official_docs_high_trust_is_downgraded_by_server_rules() -> None:
    claim = _claim(
        claim_id="claim_fake_docs",
        tool_id="github-cli",
        evidence=[
            _evidence_dict(
                evidence_id="ev_fake_docs",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://docs.example.com/pretending-to-be-github-docs",
                trust_level=TrustLevel.HIGH,
            )
        ],
    )

    breakdown = compute_confidence_breakdown(claim)

    assert breakdown.score == 0.1125
    assert breakdown.components[0].reason.startswith("type=official_docs trust=low")


def test_unknown_high_trust_sources_cannot_accept_high_risk_claim(tmp_path) -> None:
    evidence = [
        _evidence_dict(
            evidence_id="ev_fake_docs",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="https://docs.example.com/critical",
            trust_level=TrustLevel.HIGH,
        ),
        _evidence_dict(
            evidence_id="ev_fake_source",
            evidence_type=EvidenceType.SOURCE_CODE,
            source_uri="https://github.com/random/fake-tool/blob/main/delete.py",
            trust_level=TrustLevel.HIGH,
        ),
    ]
    claim = _claim(
        claim_id="claim_fake_high_risk",
        subject="gh repo delete",
        statement="`gh repo delete` removes a repository.",
        tool_id="github-cli",
        risk_level=RiskLevel.CRITICAL,
        evidence=evidence,
    )
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision != OrchestratorDecision.ACCEPTED
    assert result.verification_level == VerificationLevel.L1_SCHEMA_VALID
    assert "NO_TRUSTED_SOURCE_EVIDENCE" in result.reason_codes


def test_pre_stage_runtime_and_maintainer_evidence_cannot_self_assert_high_trust(
    tmp_path,
) -> None:
    evidence = [
        _evidence_dict(
            evidence_id="ev_docs",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
        ),
        _evidence_dict(
            evidence_id="ev_review",
            evidence_type=EvidenceType.MAINTAINER_REVIEW,
            source_uri="maintainer-review://unverified/review-1",
            trust_level=TrustLevel.HIGH,
        ),
    ]
    claim = _claim(
        claim_id="claim_spoofed_review",
        subject="gh repo delete",
        statement="`gh repo delete` removes a repository.",
        tool_id="github-cli",
        risk_level=RiskLevel.CRITICAL,
        evidence=evidence,
    )
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")

    result = CanonOrchestrator(store).verify_claim(claim)

    assert result.decision == OrchestratorDecision.REJECTED
    assert result.reason_codes == ["REJECTED_PRIMARY_EVIDENCE"]


def test_source_repo_allowlist_requires_exact_repo_path() -> None:
    claim = _claim(
        claim_id="claim_prefix_attack",
        tool_id="github-cli",
        evidence=[
            _evidence_dict(
                evidence_id="ev_prefix_attack",
                evidence_type=EvidenceType.SOURCE_CODE,
                source_uri="https://github.com/cli/client/blob/main/delete.go",
                trust_level=TrustLevel.HIGH,
            )
        ],
    )

    breakdown = compute_confidence_breakdown(claim)

    assert breakdown.score == 0.1
    assert breakdown.components[0].reason.startswith("type=source_code trust=low")


def test_orchestrator_persists_breakdown_through_store(tmp_path) -> None:
    """The breakdown survives the round-trip through SQLite, including caps and penalties."""
    claim = _claim(
        claim_id="claim_persist",
        risk_level=RiskLevel.CRITICAL,
        subject="gh repo delete",
        statement="`gh repo delete` removes a repository.",
        tool_id="github-cli",
        evidence=[
            _evidence_dict(
                evidence_id="ev_persist",
                evidence_type=EvidenceType.OFFICIAL_DOCS,
                source_uri="https://docs.github.com/cli/manual/gh_repo_delete",
            )
        ],
    )
    store = ClaimStore(database_url=f"sqlite:///{tmp_path / 'agentatlas.db'}")
    store.create(claim)

    result = CanonOrchestrator(store).verify_claim(claim)
    store.save_verification_result(result)

    reloaded = store.get_latest_verification_result(claim.claim_id)
    assert reloaded is not None
    assert reloaded.confidence_breakdown.model_dump(mode="json") == result.confidence_breakdown.model_dump(
        mode="json"
    )
    assert reloaded.confidence_band == result.confidence_band

    reloaded_claim = store.get(claim.claim_id)
    assert reloaded_claim is not None
    assert reloaded_claim.confidence_band == result.confidence_band
