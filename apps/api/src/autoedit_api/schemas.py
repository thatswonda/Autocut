"""HTTP request/response models. Errors share one envelope: ``{"error": {code, message, details}}``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoedit_schemas.media import ACCEPTED_CONTAINERS, MediaInfo


class ApiError(Exception):
    def __init__(self, http_status: int, code: str, message: str, /, **details: Any):
        self.status = http_status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# -------------------------------------------------------------------------------------- uploads


class UploadCreate(_In):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    content_type: str | None = Field(default=None, max_length=128)


class UploadComplete(_In):
    sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", description="Optional end-to-end check"
    )


class UploadOut(BaseModel):
    id: str
    filename: str
    size_bytes: int
    offset: int
    status: Literal["uploading", "complete", "failed"]
    chunk_size: int
    media: MediaInfo | None = None
    error_code: str | None = None
    error: str | None = None
    created_at: datetime


ACCEPTED_EXTENSIONS = tuple(f".{c}" for c in ACCEPTED_CONTAINERS)


# ------------------------------------------------------------------------------------- projects


class EditOptions(_In):
    goal: Literal["short_vertical", "youtube", "promo"] = "short_vertical"
    target_duration_s: float | None = Field(default=None, gt=0, le=3600)
    style: str = Field(default="energetic", max_length=64)
    language: str | None = Field(default=None, max_length=16)
    captions: bool = True
    broll: bool = True
    sfx: bool = True
    color_grade: bool = True
    music: bool = True
    allow_reorder: bool = False


class ProjectCreate(_In):
    mode: Literal["edit", "motion"]
    title: str = Field(default="", max_length=200)
    prompt: str = Field(default="", max_length=8000)
    options: EditOptions = Field(default_factory=EditOptions)
    upload_ids: list[str] = Field(default_factory=list, max_length=100)


class ClipAdd(_In):
    upload_id: str
    position: int | None = Field(default=None, ge=1, description="1-based; default appends")


class ClipOrder(_In):
    clip_ids: list[str] = Field(min_length=1)


class ClipOut(BaseModel):
    id: str
    upload_id: str
    filename: str
    position: int
    duration_s: float
    media: MediaInfo | None
    status: Literal["queued", "processing", "ready", "failed"]
    stage: str | None
    progress: float
    error_code: str | None
    error: str | None
    signals_url: str | None
    proxy_url: str | None


class LimitsOut(BaseModel):
    plan: str
    max_total_raw_s: float
    max_clips: int
    projects_per_month: int | None
    watermark: bool


class LengthMeter(BaseModel):
    total_raw_s: float
    limit_s: float
    remaining_s: float
    fraction: float
    clip_count: int
    max_clips: int


class ProjectOut(BaseModel):
    id: str
    mode: Literal["edit", "motion"]
    title: str
    prompt: str
    options: dict[str, Any]
    status: Literal["empty", "analyzing", "ready", "partial", "failed"]
    stage: str | None
    progress: float
    meter: LengthMeter
    clips: list[ClipOut]
    created_at: datetime
    updated_at: datetime


class ProjectSummary(BaseModel):
    id: str
    mode: str
    title: str
    status: str
    clip_count: int
    total_raw_s: float
    created_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectSummary]
    next_cursor: str | None


class MeOut(BaseModel):
    id: str
    plan: str
    limits: LimitsOut
    projects_this_month: int
