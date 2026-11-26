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


AspectStr = Literal["9:16", "1:1", "16:9", "4:5"]


class EditOptions(_In):
    goal: Literal["short_vertical", "youtube", "promo"] = "short_vertical"
    aspect: AspectStr = "9:16"
    target_duration_s: float | None = Field(default=None, gt=0, le=3600)
    style: str = Field(default="energetic", max_length=64)
    color_preset: str = Field(default="auto", max_length=32)
    language: str | None = Field(default=None, max_length=16)
    captions: bool | None = None
    caption_style: str | None = Field(default=None, max_length=32)
    music_asset_id: str | None = Field(default=None, max_length=64)
    broll: bool = True
    sfx: bool = True
    color_grade: bool = True
    music: bool = True
    allow_reorder: bool = False
    intro_template_id: str | None = None
    outro_template_id: str | None = None


class MotionOptions(_In):
    aspect: AspectStr = "9:16"
    duration_s: float = Field(default=20, ge=6, le=60)
    style: str = Field(default="bold_pop", max_length=32)
    language: str | None = Field(default=None, max_length=16)
    goal: Literal["promo", "product_launch", "explainer", "ad", "other"] = "promo"
    brand_name: str = Field(default="", max_length=120)
    intro_template_id: str | None = None
    outro_template_id: str | None = None
    voiceover: bool = False
    voice: str = Field(default="female_us", max_length=32)
    voiceover_captions: bool = False
    music_asset_id: str | None = Field(default=None, max_length=64)


class ProjectCreate(_In):
    mode: Literal["edit", "motion"]
    title: str = Field(default="", max_length=200)
    prompt: str = Field(default="", max_length=8000)
    options: dict[str, Any] = Field(default_factory=dict)
    upload_ids: list[str] = Field(default_factory=list, max_length=100)
    source_urls: list[str] = Field(default_factory=list, max_length=5)
    asset_ids: list[str] = Field(default_factory=list, max_length=12)
    brand_authorized: bool = False


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


class AssetOut(BaseModel):
    id: str
    kind: str
    filename: str
    width: int | None
    height: int | None
    source: str
    url: str
    duration_s: float | None = None


class MotionOut(BaseModel):
    status: Literal["queued", "planning", "ready", "failed"]
    stage: str | None
    progress: float
    error_code: str | None
    error: str | None
    brand_kit: dict[str, Any] | None
    composition: dict[str, Any] | None
    version: int
    pending_scene: str | None
    cost_usd: float
    assets: list[AssetOut]


class RenderOut(BaseModel):
    id: str
    kind: str
    status: Literal["queued", "processing", "ready", "failed"]
    stage: str | None
    progress: float
    error_code: str | None
    error: str | None
    settings: dict[str, Any]
    report: dict[str, Any] | None
    duration_s: float | None
    width: int | None
    height: int | None
    video_url: str | None
    download_url: str | None
    poster_url: str | None
    created_at: datetime


class RenderCreate(_In):
    resolution: Literal[720, 1080] = 720
    aspect: AspectStr | None = None
    color_preset: str | None = Field(default=None, max_length=32)
    target_duration_s: float | None = Field(default=None, gt=0, le=3600)
    intro_template_id: str | None = None
    outro_template_id: str | None = None
    prompt: str | None = Field(default=None, max_length=4000, description="New instructions for the edit")
    plan: dict[str, Any] | None = Field(default=None, description="An edited plan from a previous render")
    captions: bool | None = None
    caption_style: str | None = Field(default=None, max_length=32)
    music_asset_id: str | None = Field(default=None, max_length=64)


class SceneRegenerate(_In):
    note: str = Field(default="", max_length=600)


class BrandKitUpdate(_In):
    name: str | None = Field(default=None, max_length=200)
    colors: dict[str, str] | None = None
    logo_asset: str | None = None


class TemplateCreate(_In):
    render_id: str
    t_in: float = Field(ge=0)
    t_out: float = Field(gt=0)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["intro", "outro", "clip"] = "clip"


class TemplateOut(BaseModel):
    id: str
    name: str
    kind: str
    duration_s: float
    video_url: str
    poster_url: str | None
    created_at: datetime


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
    status: Literal[
        "empty", "analyzing", "ready", "partial", "failed", "planning", "review", "rendering", "done"
    ]
    stage: str | None
    progress: float
    meter: LengthMeter
    clips: list[ClipOut]
    motion: MotionOut | None = None
    renders: list[RenderOut] = Field(default_factory=list)
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
    billing_enabled: bool
    ai_enabled: bool
    limits: LimitsOut
    projects_this_month: int
