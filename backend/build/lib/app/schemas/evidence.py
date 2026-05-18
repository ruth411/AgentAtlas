from datetime import datetime, timezone
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import EvidenceType, TrustLevel

EXCERPT_MAX_LENGTH = 8000
SOURCE_URI_MAX_LENGTH = 2048
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _validate_source_uri(value: str) -> str:
    if "://" not in value:
        raise ValueError("source_uri must include a URI scheme")
    if len(value) > SOURCE_URI_MAX_LENGTH:
        raise ValueError(f"source_uri must be at most {SOURCE_URI_MAX_LENGTH} chars")
    return value


def _validate_hash(value: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError("hash must use sha256:<64 lowercase hex chars> format")
    return value


def _validate_evidence_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not EVIDENCE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "evidence_id must match [A-Za-z0-9_.-]{1,128}; got "
            f"{value!r}"
        )
    return value


def _validate_captured_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    if value > datetime.now(timezone.utc):
        raise ValueError("captured_at must not be in the future")
    return value


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1, max_length=128)
    evidence_type: EvidenceType
    source_uri: str = Field(min_length=1, max_length=SOURCE_URI_MAX_LENGTH)
    excerpt: str = Field(min_length=1, max_length=EXCERPT_MAX_LENGTH)
    hash: str = Field(min_length=1)
    captured_at: datetime
    trust_level: TrustLevel

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        return _validate_evidence_id(value) or value

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

    evidence_id: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_type: EvidenceType
    source_uri: str = Field(min_length=1, max_length=SOURCE_URI_MAX_LENGTH)
    excerpt: str = Field(min_length=1, max_length=EXCERPT_MAX_LENGTH)
    hash: str = Field(min_length=1)
    captured_at: datetime
    trust_level: TrustLevel

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str | None) -> str | None:
        return _validate_evidence_id(value)

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
