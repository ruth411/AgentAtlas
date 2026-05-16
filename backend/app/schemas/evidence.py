from datetime import datetime, timezone
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import EvidenceType, TrustLevel

EXCERPT_MAX_LENGTH = 8000
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _validate_source_uri(value: str) -> str:
    if "://" not in value:
        raise ValueError("source_uri must include a URI scheme")
    return value


def _validate_hash(value: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError("hash must use sha256:<64 lowercase hex chars> format")
    return value


def _validate_captured_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    if value > datetime.now(timezone.utc):
        raise ValueError("captured_at must not be in the future")
    return value


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1)
    evidence_type: EvidenceType
    source_uri: str = Field(min_length=1)
    excerpt: str = Field(min_length=1, max_length=EXCERPT_MAX_LENGTH)
    hash: str = Field(min_length=1)
    captured_at: datetime
    trust_level: TrustLevel

    @field_validator("source_uri")
    @classmethod
    def validate_source_uri(cls, value: str) -> str:
        return _validate_source_uri(value)

    @field_validator("hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return _validate_hash(value)

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return _validate_captured_at(value)


class EvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str | None = Field(default=None, min_length=1)
    evidence_type: EvidenceType
    source_uri: str = Field(min_length=1)
    excerpt: str = Field(min_length=1, max_length=EXCERPT_MAX_LENGTH)
    hash: str = Field(min_length=1)
    captured_at: datetime
    trust_level: TrustLevel

    @field_validator("source_uri")
    @classmethod
    def validate_source_uri(cls, value: str) -> str:
        return _validate_source_uri(value)

    @field_validator("hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return _validate_hash(value)

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return _validate_captured_at(value)
