import json
import re
from pathlib import Path

from app.schemas.risk import RiskDimension


REPO_ROOT = Path(__file__).resolve().parents[2]
RISK_MODEL = REPO_ROOT / "contracts/risk_model.v1.json"
STAGE_0_CONTRACT = REPO_ROOT / "contracts/ayiru_stage_0.v1.json"


def _risk_model() -> dict:
    with RISK_MODEL.open() as handle:
        return json.load(handle)


def test_risk_model_contract_is_versioned() -> None:
    assert _risk_model()["version"] == 1


def test_stage_0_tools_have_risk_rules() -> None:
    with STAGE_0_CONTRACT.open() as handle:
        stage_0 = json.load(handle)

    tools_with_rules = set(_risk_model()["tools"])
    expected_tools = {tool["tool_id"] for tool in stage_0["initial_tools"]}

    assert expected_tools.issubset(tools_with_rules)


def test_dimensions_match_schema_enum() -> None:
    model = _risk_model()
    dimensions = {item.value for item in RiskDimension}

    assert set(model["dimensions"]) == dimensions
    for rule in model["universal"]:
        assert set(rule["dimensions"]).issubset(dimensions)
    for rules in model["tools"].values():
        for rule in rules:
            assert set(rule["dimensions"]).issubset(dimensions)


def test_every_pattern_compiles() -> None:
    model = _risk_model()
    for rule in model["universal"]:
        re.compile(rule["pattern"])
    for rules in model["tools"].values():
        for rule in rules:
            re.compile(rule["pattern"])


def test_canonical_rules_are_readable_from_json() -> None:
    model = _risk_model()
    all_rules = model["universal"] + [
        rule for rules in model["tools"].values() for rule in rules
    ]
    rule_ids = {rule["id"] for rule in all_rules}

    assert {"git.status", "gh.repo_delete", "vercel.prod"}.issubset(rule_ids)
