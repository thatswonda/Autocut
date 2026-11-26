"""``signals.json``: output of the Stage A local signal pass (PRD 5.2). No AI cost.

Per-second rows cover the half-open interval ``[t, t + 1)``. Boxes and points are normalised to the
display frame (0..1, origin top-left, after rotation).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import NonNegTime, StrictModel
from .media import MediaInfo

SIGNALS_VERSION: Literal[1] = 1


class Box(StrictModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(ge=0.0, le=1.0)
    h: float = Field(ge=0.0, le=1.0)
    score: float = Field(default=1.0, ge=0.0, le=1.0)


class Point(StrictModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class Range(StrictModel):
    t_in: NonNegTime
    t_out: NonNegTime


class FlaggedRange(Range):
    reason: str


class SecondMetrics(StrictModel):
    t: int = Field(ge=0)
    # Video
    motion: float = Field(ge=0.0, le=1.0, description="Share of pixels that changed vs the previous frame")
    sharpness: float = Field(
        ge=0.0, le=1.0, description="Content-normalised detail, 0..1 (sharp ~0.9, blurred <0.75; 0 if flat)"
    )
    blurry: bool
    shake: float = Field(ge=0.0, description="High-frequency camera jitter, fraction of frame diagonal")
    shaky: bool
    brightness: float = Field(ge=0.0, le=1.0, description="Mean luma, 0..1")
    exposure: Literal["ok", "under", "over"]
    black: bool
    scene_cut: bool = Field(description="A scene boundary falls inside this second")
    # Subject
    faces: list[Box] = Field(default_factory=list, max_length=5)
    person_count: int = Field(default=0, ge=0)
    subject: Box | None = Field(default=None, description="Union of the primary face and body")
    subject_center: Point | None = None
    # Audio (null when the clip has no audio track)
    loudness_lufs: float | None = Field(default=None, description="Mean momentary loudness (EBU R128)")
    silent: bool | None = None
    speech: float | None = Field(default=None, ge=0.0, le=1.0, description="Share of voiced 30 ms frames")
    music: bool | None = None
    # Derived
    dead: bool = Field(description="Low motion and no speech (or black)")


class Scene(Range):
    index: int = Field(ge=0)


class AudioSummary(StrictModel):
    integrated_lufs: float | None
    loudness_range_lu: float | None
    peak_dbfs: float | None
    speech_ratio: float = Field(ge=0.0, le=1.0)
    music_ratio: float = Field(ge=0.0, le=1.0)
    tempo_bpm: float | None


class VideoSummary(StrictModel):
    mean_motion: float
    median_sharpness: float
    blurry_ratio: float = Field(ge=0.0, le=1.0)
    shaky_ratio: float = Field(ge=0.0, le=1.0)
    face_ratio: float = Field(ge=0.0, le=1.0, description="Share of seconds with at least one face")
    person_ratio: float = Field(ge=0.0, le=1.0)
    scene_count: int


class Summary(StrictModel):
    dead_ratio: float = Field(ge=0.0, le=1.0)
    dead_seconds: float = Field(ge=0.0)
    usable_seconds: float = Field(ge=0.0)
    video: VideoSummary
    audio: AudioSummary | None


class AnalysisInfo(StrictModel):
    signals_version: int = SIGNALS_VERSION
    pipeline_version: str
    analysis_fps: float
    analysis_width: int
    detectors: dict[str, str] = Field(description="Which implementation produced each signal")
    timings_s: dict[str, float]
    params: dict[str, float | int | str | bool]


class Signals(StrictModel):
    version: Literal[1] = SIGNALS_VERSION
    clip_id: str
    source_sha256: str
    media: MediaInfo
    analysis: AnalysisInfo
    per_second: list[SecondMetrics]
    scenes: list[Scene]
    scene_cuts: list[float] = Field(description="Times of detected cuts, seconds")
    silences: list[Range]
    speech_ranges: list[Range]
    music_ranges: list[Range]
    beats: list[float]
    dead_zones: list[FlaggedRange]
    quality_flags: list[FlaggedRange] = Field(description="Blur, shake, black and exposure problems")
    summary: Summary
