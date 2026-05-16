from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from app.schemas.enums import RISK_ORDER, RiskLevel
from app.schemas.risk import RiskAssessment, RiskDimension, RiskMatch


_RISK_MODEL_CONTRACT = Path(__file__).resolve().parents[3] / "contracts/risk_model.v1.json"
_DIMENSION_ORDER = {dimension: index for index, dimension in enumerate(RiskDimension)}


def compute_risk_assessment(
    subject: str,
    statement: str = "",
    tool_id: str | None = None,
) -> RiskAssessment:
    """Compute deterministic risk from versioned rules.

    If `tool_id` is omitted, only universal rules run. Tool-scoped rules are
    intentionally not guessed from prose, because the caller owns tool context.
    """

    model = _risk_model()
    text = f"{subject}\n{statement}"
    rules = list(model["universal"])
    if tool_id is not None:
        rules.extend(model["tools"].get(tool_id.strip().lower(), []))

    matches: list[RiskMatch] = []
    for rule in rules:
        match = re.search(rule["pattern"], text)
        if match is None:
            continue
        matches.append(
            RiskMatch(
                rule_id=rule["id"],
                pattern=rule["pattern"],
                risk_level=RiskLevel(rule["risk"]),
                dimensions=[RiskDimension(item) for item in rule["dimensions"]],
                reason=rule["reason"],
                matched_text=match.group(0).strip(),
            )
        )

    if not matches:
        fallback = model["fallback"]
        risk_level = RiskLevel(fallback["risk_level"])
        return RiskAssessment(
            risk_level=risk_level,
            reasons=[fallback["reason"]],
            requires_confirmation=_requires_confirmation(risk_level),
            safe_to_auto_execute=_safe_to_auto_execute(risk_level),
            dimensions=[RiskDimension(item) for item in fallback["dimensions"]],
            matched_rule_ids=[],
            matches=[],
        )

    matches_by_id = sorted(matches, key=lambda item: item.rule_id)
    risk_level = max((item.risk_level for item in matches), key=lambda item: RISK_ORDER[item])
    dimensions = sorted(
        {dimension for item in matches for dimension in item.dimensions},
        key=lambda item: _DIMENSION_ORDER[item],
    )

    return RiskAssessment(
        risk_level=risk_level,
        reasons=[item.reason for item in matches_by_id],
        requires_confirmation=_requires_confirmation(risk_level),
        safe_to_auto_execute=_safe_to_auto_execute(risk_level),
        dimensions=dimensions,
        matched_rule_ids=[item.rule_id for item in matches_by_id],
        matches=matches_by_id,
    )


@cache
def _risk_model() -> dict[str, Any]:
    with _RISK_MODEL_CONTRACT.open() as handle:
        data = json.load(handle)
    _validate_risk_model(data)
    return data


def _validate_risk_model(data: dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("risk model contract must have version=1")

    dimensions = data.get("dimensions")
    if not isinstance(dimensions, list) or set(dimensions) != {item.value for item in RiskDimension}:
        raise ValueError("risk model dimensions must match RiskDimension enum values")

    fallback = data.get("fallback")
    if not isinstance(fallback, dict):
        raise ValueError("risk model must define fallback")
    _validate_risk_level(fallback.get("risk_level"), "fallback")
    _validate_dimensions(fallback.get("dimensions"), "fallback")
    if not fallback.get("reason"):
        raise ValueError("risk model fallback must define reason")

    universal = data.get("universal")
    tools = data.get("tools")
    if not isinstance(universal, list) or not universal:
        raise ValueError("risk model must define universal rules")
    if not isinstance(tools, dict) or not tools:
        raise ValueError("risk model must define tool rules")

    seen_rule_ids: set[str] = set()
    for rule in universal:
        _validate_rule(rule, seen_rule_ids, "universal")

    for tool_id, rules in tools.items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("risk model has invalid tool id")
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"risk model tool '{tool_id}' must define rules")
        for rule in rules:
            _validate_rule(rule, seen_rule_ids, tool_id)


def _validate_rule(rule: dict[str, Any], seen_rule_ids: set[str], scope: str) -> None:
    if not isinstance(rule, dict):
        raise ValueError(f"risk model scope '{scope}' has invalid rule")
    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or not rule_id:
        raise ValueError(f"risk model scope '{scope}' has rule missing id")
    if rule_id in seen_rule_ids:
        raise ValueError(f"risk model duplicate rule id '{rule_id}'")
    seen_rule_ids.add(rule_id)
    pattern = rule.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(f"risk model rule '{rule_id}' missing pattern")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"risk model rule '{rule_id}' has invalid regex") from exc
    _validate_risk_level(rule.get("risk"), rule_id)
    _validate_dimensions(rule.get("dimensions"), rule_id)
    if not rule.get("reason"):
        raise ValueError(f"risk model rule '{rule_id}' missing reason")


def _validate_risk_level(value: object, scope: str) -> None:
    try:
        RiskLevel(value)
    except ValueError as exc:
        raise ValueError(f"risk model '{scope}' has invalid risk level") from exc


def _validate_dimensions(value: object, scope: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"risk model '{scope}' must define dimensions")
    try:
        for item in value:
            RiskDimension(item)
    except ValueError as exc:
        raise ValueError(f"risk model '{scope}' has invalid dimension") from exc


def _requires_confirmation(risk_level: RiskLevel) -> bool:
    return risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}


def _safe_to_auto_execute(risk_level: RiskLevel) -> bool:
    return risk_level in {RiskLevel.NONE, RiskLevel.LOW}
