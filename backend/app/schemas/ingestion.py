from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import IngestionArtifactType, IngestionStatus

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _require_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            "identifier must match [A-Za-z0-9_.-]{1,128}; got "
            f"{value!r}"
        )
    return value


class CliIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1, max_length=512)
    submitted_by: str = Field(default="cli-ingestion-agent", min_length=1, max_length=128)
    verify: bool = True


class CliIngestionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    capture_argv: list[str] = Field(default_factory=list)
    created_claim_ids: list[str] = Field(default_factory=list)
    verification_result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class IngestionRun(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    tool_id: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1, max_length=512)
    status: IngestionStatus
    submitted_by: str = Field(min_length=1, max_length=128)
    created_at: datetime
    completed_at: datetime | None = None
    summary: CliIngestionSummary = Field(default_factory=CliIngestionSummary)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("created_at", "completed_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_tz_aware(value) if value is not None else None


class RawIngestionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    artifact_type: IngestionArtifactType
    source_uri: str = Field(min_length=1, max_length=2048)
    raw_content: str = Field(min_length=1)
    hash: str = Field(min_length=1)
    captured_at: datetime

    @field_validator("artifact_id", "run_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


class CliIngestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    status: IngestionStatus
    created_claim_ids: list[str] = Field(default_factory=list)
    verification_result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# -------- Stage 7b: Docs Ingestion --------


class DocsIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=2048)
    submitted_by: str = Field(default="docs-ingestion-agent", min_length=1, max_length=128)
    verify: bool = True


class DocsIngestionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(default="", max_length=2048)
    final_url: str = Field(default="", max_length=2048)
    redirect_chain: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    content_length: int = 0
    created_claim_ids: list[str] = Field(default_factory=list)
    verification_result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DocsIngestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    status: IngestionStatus
    cache_hit: bool = False
    final_url: str = Field(default="", max_length=2048)
    redirect_chain: list[str] = Field(default_factory=list)
    content_length: int = 0
    created_claim_ids: list[str] = Field(default_factory=list)
    verification_result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DocsFetchCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    etag: str | None = Field(default=None, max_length=512)
    last_modified: str | None = Field(default=None, max_length=128)
    body_hash: str = Field(min_length=1, max_length=128)
    last_fetched_at: datetime
    last_artifact_id: str | None = Field(default=None, max_length=128)

    @field_validator("last_fetched_at")
    @classmethod
    def validate_last_fetched_at(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)
