"""Media facts reported by ffprobe for an uploaded clip."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import StrictModel

ACCEPTED_CONTAINERS = ("mp4", "mov", "mkv", "webm")


class MediaInfo(StrictModel):
    container: Literal["mp4", "mov", "mkv", "webm"]
    duration_s: float = Field(gt=0)
    size_bytes: int = Field(ge=0)
    bitrate_kbps: float | None = None
    # Coded dimensions, as stored in the stream.
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0
    # Dimensions as the viewer sees them, after applying rotation.
    display_width: int = Field(gt=0)
    display_height: int = Field(gt=0)
    fps: float = Field(gt=0)
    variable_frame_rate: bool = False
    video_codec: str
    pixel_format: str | None = None
    has_audio: bool
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None

    @property
    def orientation(self) -> Literal["landscape", "portrait", "square"]:
        if self.display_width > self.display_height:
            return "landscape"
        if self.display_width < self.display_height:
            return "portrait"
        return "square"

    @property
    def aspect_ratio(self) -> float:
        return self.display_width / self.display_height
