from pydantic import BaseModel, ConfigDict

from app.schemas.enums import RiskLevel


class SafetyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel
    auto_execute_allowed: bool
    requires_human_confirmation: bool
    notes: str


class SafetyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[SafetyRule]

    @classmethod
    def default(cls) -> "SafetyPolicy":
        return cls(
            rules=[
                SafetyRule(
                    risk_level=RiskLevel.NONE,
                    auto_execute_allowed=True,
                    requires_human_confirmation=False,
                    notes="Pure read/no-op actions",
                ),
                SafetyRule(
                    risk_level=RiskLevel.LOW,
                    auto_execute_allowed=True,
                    requires_human_confirmation=False,
                    notes="Local read-only inspection",
                ),
                SafetyRule(
                    risk_level=RiskLevel.MEDIUM,
                    auto_execute_allowed=False,
                    requires_human_confirmation=True,
                    notes="Creates temporary, local, or reversible state",
                ),
                SafetyRule(
                    risk_level=RiskLevel.HIGH,
                    auto_execute_allowed=False,
                    requires_human_confirmation=True,
                    notes="Remote state mutation, deployment, billing, or permissions changes",
                ),
                SafetyRule(
                    risk_level=RiskLevel.CRITICAL,
                    auto_execute_allowed=False,
                    requires_human_confirmation=True,
                    notes="Delete, irreversible mutation, secret exposure, or production changes",
                ),
            ]
        )

