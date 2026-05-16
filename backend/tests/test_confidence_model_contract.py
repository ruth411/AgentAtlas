import json
from pathlib import Path

from app.schemas.enums import ConfidenceBand, EvidenceType, TrustLevel
from app.services.confidence_scorer import _confidence_model


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIDENCE_MODEL = REPO_ROOT / "contracts/confidence_model.v1.json"


def _contract() -> dict:
    with CONFIDENCE_MODEL.open() as handle:
        return json.load(handle)


def test_confidence_model_contract_is_versioned() -> None:
    assert _contract()["version"] == 1


def test_confidence_weights_cover_schema_enums() -> None:
    contract = _contract()

    assert set(contract["evidence_type_weights"]) == {item.value for item in EvidenceType}
    assert set(contract["trust_multipliers"]) == {item.value for item in TrustLevel}


def test_confidence_band_thresholds_are_monotonic_and_cover_zero() -> None:
    thresholds = _contract()["band_thresholds"]

    assert [item["min"] for item in thresholds] == sorted(
        [item["min"] for item in thresholds], reverse=True
    )
    assert thresholds[-1] == {"min": 0.0, "band": ConfidenceBand.NONE.value}
    assert thresholds[0]["band"] == ConfidenceBand.STRONG.value


def test_confidence_contract_matches_stage_4_legacy_values() -> None:
    contract = _contract()

    assert contract["evidence_type_weights"]["official_docs"] == 0.45
    assert contract["evidence_type_weights"]["maintainer_review"] == 0.5
    assert contract["evidence_type_weights"]["package_metadata"] == 0.25
    assert contract["trust_multipliers"] == {"high": 1.0, "medium": 0.6, "low": 0.25}
    assert contract["diminishing_factors"] == [1.0, 0.4]
    assert contract["caps"] == {
        "single_evidence_type": 0.65,
        "all_low_trust": 0.45,
        "high_risk_single_stream": 0.5,
        "high_risk_no_diverse_trusted": 0.8,
        "conflict_detected": 0.4,
        "understated_risk": 0.5,
    }


def test_confidence_model_loader_validates_and_caches() -> None:
    assert _confidence_model() is _confidence_model()
