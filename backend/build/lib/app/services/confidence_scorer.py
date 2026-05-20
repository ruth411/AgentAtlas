"""Stage 4 confidence scorer.

Design rules:

* Confidence is a function of *structured* evidence properties only.
  Excerpt prose is never read.
* Each unique evidence record contributes a weight determined by its
  type and trust level.
* Duplicate `source_uri` or duplicate `hash` is counted once.
* For each evidence type, only the first two occurrences contribute;
  the second occurrence contributes at 40% of the first. This stops
  low-value spam from inflating the score.
* Hard caps apply for: empty evidence, single-evidence-type submissions,
  all-low-trust submissions, conflict-detected claims, and high/critical
  risk submissions without diverse trusted evidence.
* The final result is reproducible from inputs and is returned as a
  `ConfidenceBreakdown` so consumers can inspect every contribution and
  every cap that was applied.

The legacy `score_claim_confidence(claim, has_conflict)` callable still
returns a float for backwards compatibility; it delegates to the new
`compute_confidence_breakdown`.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from app.schemas.claim import KnowledgeClaim
from app.schemas.confidence import ConfidenceBreakdown, ConfidenceComponent
from app.schemas.enums import ConfidenceBand, EvidenceType, RiskLevel, TrustLevel
from app.schemas.evidence import Evidence
from app.services.contract_paths import contract_path
from app.services.evidence_trust import normalize_claim_evidence_trust


_CONFIDENCE_MODEL_CONTRACT = contract_path("confidence_model.v1.json")


def band_for_score(score: float) -> ConfidenceBand:
    for threshold, band in _band_thresholds():
        if score >= threshold:
            return band
    return ConfidenceBand.NONE


def confidence_caps() -> dict[str, float]:
    return dict(_confidence_model()["caps"])


def _deduped_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Drop repeated source URIs or hashes. Keeps first occurrence."""
    seen_source_uris: set[str] = set()
    seen_hashes: set[str] = set()
    out: list[Evidence] = []
    for item in evidence:
        if item.source_uri in seen_source_uris or item.hash in seen_hashes:
            continue
        seen_source_uris.add(item.source_uri)
        seen_hashes.add(item.hash)
        out.append(item)
    return out


def compute_confidence_breakdown(
    claim: KnowledgeClaim, *, has_conflict: bool = False
) -> ConfidenceBreakdown:
    claim = normalize_claim_evidence_trust(claim)
    components: list[ConfidenceComponent] = []
    caps_applied: list[str] = []
    penalties: list[str] = []

    if not claim.evidence:
        components.append(
            ConfidenceComponent(
                source="no_evidence",
                delta=0.0,
                reason="Claim has no evidence; confidence is zero.",
            )
        )
        return ConfidenceBreakdown(
            score=0.0,
            band=ConfidenceBand.NONE,
            components=components,
            caps_applied=["empty_evidence"],
            penalties=penalties,
        )

    deduped = _deduped_evidence(claim.evidence)
    deduplicated_count = len(claim.evidence) - len(deduped)
    if deduplicated_count:
        penalties.append(
            f"Ignored {deduplicated_count} duplicate evidence record(s) (repeated source_uri or hash)."
        )

    score = 0.0
    type_occurrences: dict[EvidenceType, int] = {}
    evidence_type_weights = _evidence_type_weights()
    trust_multipliers = _trust_multipliers()
    diminishing_factors = _diminishing_factors()
    diverse_trusted_types = _diverse_trusted_types()
    caps = confidence_caps()

    for item in deduped:
        occurrence_index = type_occurrences.get(item.evidence_type, 0)
        factor = (
            diminishing_factors[occurrence_index]
            if occurrence_index < len(diminishing_factors)
            else 0.0
        )
        type_occurrences[item.evidence_type] = occurrence_index + 1

        weight = evidence_type_weights[item.evidence_type]
        multiplier = trust_multipliers[item.trust_level]
        delta = round(weight * multiplier * factor, 4)
        score += delta

        components.append(
            ConfidenceComponent(
                source=f"evidence:{item.evidence_id}",
                delta=delta,
                reason=(
                    f"type={item.evidence_type.value} trust={item.trust_level.value} "
                    f"occurrence={occurrence_index + 1} factor={factor}"
                ),
            )
        )

    distinct_types = set(type_occurrences)
    distinct_stream_keys = {(item.evidence_type.value, item.source_uri) for item in deduped}

    if len(distinct_types) == 1:
        cap = caps["single_evidence_type"]
        if score > cap:
            penalties.append(
                f"Single-evidence-type submissions are capped at {cap:.2f} (no diversity)."
            )
            score = cap
            caps_applied.append(f"single_evidence_type:{cap:.2f}")

    if all(item.trust_level == TrustLevel.LOW for item in deduped):
        cap = caps["all_low_trust"]
        if score > cap:
            penalties.append(f"All-low-trust evidence is capped at {cap:.2f}.")
            score = cap
            caps_applied.append(f"all_low_trust:{cap:.2f}")

    if claim.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        if len(distinct_stream_keys) < 2:
            cap = caps["high_risk_single_stream"]
            if score > cap:
                penalties.append(
                    "High/critical-risk claims with fewer than two distinct evidence streams "
                    f"are capped at {cap:.2f}."
                )
                score = cap
                caps_applied.append(f"high_risk_single_stream:{cap:.2f}")

        has_diverse_trusted = any(
            item.evidence_type in diverse_trusted_types for item in deduped
        )
        if not has_diverse_trusted:
            cap = caps["high_risk_no_diverse_trusted"]
            if score > cap:
                penalties.append(
                    "High/critical-risk claims without strong-trust evidence (official_docs, "
                    "maintainer_review, sandbox_execution, openapi_schema, source_code, or "
                    f"man_page) are capped at {cap:.2f}."
                )
                score = cap
                caps_applied.append(f"high_risk_no_diverse_trusted:{cap:.2f}")

    if has_conflict:
        cap = caps["conflict_detected"]
        penalties.append(
            f"Conflict detected with existing claim; confidence capped at {cap:.2f}."
        )
        if score > cap:
            score = cap
        caps_applied.append(f"conflict_detected:{cap:.2f}")

    score = max(0.0, min(round(score, 4), 1.0))
    band = band_for_score(score)

    return ConfidenceBreakdown(
        score=score,
        band=band,
        components=components,
        caps_applied=caps_applied,
        penalties=penalties,
    )


def score_claim_confidence(claim: KnowledgeClaim, *, has_conflict: bool = False) -> float:
    """Backwards-compatible float scorer; delegates to the breakdown."""
    return compute_confidence_breakdown(claim, has_conflict=has_conflict).score


@cache
def _confidence_model() -> dict[str, Any]:
    with _CONFIDENCE_MODEL_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_confidence_model(data)
    return data


def _evidence_type_weights() -> dict[EvidenceType, float]:
    return {
        EvidenceType(key): float(value)
        for key, value in _confidence_model()["evidence_type_weights"].items()
    }


def _trust_multipliers() -> dict[TrustLevel, float]:
    return {
        TrustLevel(key): float(value)
        for key, value in _confidence_model()["trust_multipliers"].items()
    }


def _diminishing_factors() -> tuple[float, ...]:
    return tuple(float(value) for value in _confidence_model()["diminishing_factors"])


def _diverse_trusted_types() -> frozenset[EvidenceType]:
    return frozenset(
        EvidenceType(value) for value in _confidence_model()["diverse_trusted_types"]
    )


def _band_thresholds() -> tuple[tuple[float, ConfidenceBand], ...]:
    return tuple(
        (float(item["min"]), ConfidenceBand(item["band"]))
        for item in _confidence_model()["band_thresholds"]
    )


def _validate_confidence_model(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("confidence model contract must have version=1")

    weights = data.get("evidence_type_weights")
    multipliers = data.get("trust_multipliers")
    factors = data.get("diminishing_factors")
    diverse_types = data.get("diverse_trusted_types")
    bands = data.get("band_thresholds")
    caps = data.get("caps")

    if set(weights or {}) != {item.value for item in EvidenceType}:
        raise ValueError("confidence model weights must cover every EvidenceType")
    if set(multipliers or {}) != {item.value for item in TrustLevel}:
        raise ValueError("confidence model multipliers must cover every TrustLevel")
    if not isinstance(factors, list) or not factors or any(float(item) < 0 for item in factors):
        raise ValueError("confidence model diminishing_factors must be non-empty and non-negative")

    if not isinstance(diverse_types, list) or not diverse_types:
        raise ValueError("confidence model must define diverse_trusted_types")
    for item in diverse_types:
        EvidenceType(item)

    if not isinstance(bands, list) or not bands:
        raise ValueError("confidence model must define band_thresholds")
    thresholds = [float(item["min"]) for item in bands]
    if thresholds != sorted(thresholds, reverse=True) or thresholds[-1] != 0.0:
        raise ValueError("confidence band thresholds must be descending and end at 0.0")
    for item in bands:
        ConfidenceBand(item["band"])

    required_caps = {
        "single_evidence_type",
        "all_low_trust",
        "high_risk_single_stream",
        "high_risk_no_diverse_trusted",
        "conflict_detected",
        "understated_risk",
    }
    if set(caps or {}) != required_caps:
        raise ValueError("confidence model caps must cover every required cap")
    for value in caps.values():
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("confidence model caps must be between 0.0 and 1.0")
