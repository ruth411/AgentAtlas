from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import ConfidenceBand


class ConfidenceComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1)
    delta: float
    reason: str = Field(min_length=1)


class ConfidenceBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    score: float = Field(ge=0.0, le=1.0)
    band: ConfidenceBand
    components: list[ConfidenceComponent] = Field(default_factory=list)
    caps_applied: list[str] = Field(default_factory=list)
    penalties: list[str] = Field(default_factory=list)
