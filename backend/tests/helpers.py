from hashlib import sha256

from app.schemas.enums import RiskLevel
from app.schemas.risk import RiskAssessment


def valid_sha256(value: str) -> str:
    return f"sha256:{sha256(value.encode()).hexdigest()}"


def risk_assessment(risk_level: RiskLevel = RiskLevel.MEDIUM) -> RiskAssessment:
    return RiskAssessment(
        risk_level=risk_level,
        reasons=["test risk assessment"],
        requires_confirmation=risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL},
        safe_to_auto_execute=risk_level in {RiskLevel.NONE, RiskLevel.LOW},
        dimensions=[],
        matched_rule_ids=[],
        matches=[],
    )


def _normalize_db_type(value: object) -> str:
    name = value.__class__.__name__.lower()
    if "varchar" in name or "string" in name:
        return "String"
    if "text" in name:
        return "Text"
    if "datetime" in name:
        return "DateTime"
    if "float" in name:
        return "Float"
    if "integer" in name:
        return "Integer"
    if "boolean" in name:
        return "Boolean"
    return value.__class__.__name__
