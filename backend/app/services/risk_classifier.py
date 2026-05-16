from app.schemas.enums import RISK_ORDER, RiskLevel
from app.schemas.risk import RiskAssessment
from app.services.risk_engine import compute_risk_assessment


def classify_action(
    subject: str,
    statement: str = "",
    tool_id: str | None = None,
) -> RiskAssessment:
    return compute_risk_assessment(
        subject=subject,
        statement=statement,
        tool_id=tool_id or _infer_tool_id(subject),
    )


def is_higher_risk(actual: RiskLevel, submitted: RiskLevel) -> bool:
    return RISK_ORDER[actual] > RISK_ORDER[submitted]


def _infer_tool_id(subject: str) -> str | None:
    normalized = subject.strip().casefold()
    if normalized.startswith("git "):
        return "git"
    if normalized.startswith("gh "):
        return "github-cli"
    if normalized.startswith("docker "):
        return "docker"
    if normalized.startswith("vercel"):
        return "vercel-cli"
    if normalized.startswith("openai ") or "api.openai.com" in normalized:
        return "openai-api"
    return None
