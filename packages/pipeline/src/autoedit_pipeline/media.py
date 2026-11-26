"""FFmpeg / ffprobe wrappers: probing, proxy creation, frame and audio extraction."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from autoedit_schemas.media import MediaInfo

FFMPEG = os.environ.get("AUTOEDIT_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("AUTOEDIT_FFPROBE", "ffprobe")

# FFmpeg sizes its thread pools from the host's CPU count, not the container's limit. On a cloud host that
# reports 32+ cores, one 4K clip then needs ~1.7 GB and a 512 MB server is killed. Cap every pool.
_DEC_THREADS = os.environ.get("AUTOEDIT_FFMPEG_THREADS", "2")
FF_DECODE = ["-filter_threads", "1", "-filter_complex_threads", "1", "-threads", _DEC_THREADS]
"""Put right before an input's ``-i`` (global filter limits plus that input's decoder threads)."""
FF_X264 = ["-threads", "1", "-x264-params", "rc-lookahead=4:ref=2"]
"""Put after ``-c:v libx264``: one encoder thread and a short lookahead (~200 MB less per encode)."""

PROXY_SHORT_EDGE = 540
PROXY_MAX_FPS = 30.0


class MediaError(RuntimeError):
    """A media file is unreadable, unsupported, or an ffmpeg step failed. ``code`` is API-safe."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def require_binaries() -> None:
    for binary in (FFMPEG, FFPROBE):
        if shutil.which(binary) is None:
            raise MediaError("ffmpeg_missing", f"'{binary}' was not found on PATH")


def _run(cmd: list[str], *, timeout: float, what: str) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("timeout", f"{what} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise MediaError(f"{what}_failed", f"{what} failed: " + " | ".join(tail))
    return proc


def sha256_file(path: Path, chunk: int = 4 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------------------------- probe


def _parse_rate(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _container(format_name: str, path: Path) -> str | None:
    names = set(format_name.split(","))
    ext = path.suffix.lower().lstrip(".")
    if "matroska" in names or "webm" in names:
        # ffprobe reports "matroska,webm" for both; the extension disambiguates.
        return "webm" if ext == "webm" else "mkv"
    if names & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
        return "mov" if ext == "mov" else "mp4"
    return None


def _rotation(stream: dict[str, Any]) -> int:
    rot: float | None = None
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = float(sd["rotation"])
            break
    if rot is None and "rotate" in (stream.get("tags") or {}):
        rot = -float(stream["tags"]["rotate"])  # legacy tag is clockwise; display matrix is CCW
    if rot is None:
        return 0
    # Normalise to the clockwise rotation a viewer applies: 0, 90, 180, 270.
    cw = round(-rot) % 360
    return min((0, 90, 180, 270), key=lambda r: min(abs(r - cw), 360 - abs(r - cw)))


def probe(path: Path, *, timeout: float = 60.0) -> MediaInfo:
    """Inspect a file and return validated :class:`MediaInfo`. Raises :class:`MediaError`."""
    if not path.is_file():
        raise MediaError("not_found", f"{path} does not exist")
    proc = _run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=timeout,
        what="probe",
    )
    data = json.loads(proc.stdout or b"{}")
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    container = _container(fmt.get("format_name", ""), path)
    if container is None:
        raise MediaError(
            "unsupported_format",
            f"unsupported container '{fmt.get('format_name')}'. Accepted: mp4, mov, mkv, webm",
        )

    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise MediaError("no_video", "file has no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    if duration <= 0:
        raise MediaError("no_duration", "could not determine duration")

    avg = _parse_rate(video.get("avg_frame_rate"))
    real = _parse_rate(video.get("r_frame_rate"))
    fps = avg or real
    if fps <= 0 or fps > 1000:
        raise MediaError("bad_fps", "could not determine frame rate")
    vfr = bool(avg and real and abs(avg - real) / real > 0.02)

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise MediaError("bad_dimensions", "could not determine frame size")
    rotation = _rotation(video)
    dw, dh = (height, width) if rotation in (90, 270) else (width, height)

    bitrate = fmt.get("bit_rate")
    return MediaInfo(
        container=container,
        duration_s=round(duration, 3),
        size_bytes=int(fmt.get("size") or path.stat().st_size),
        bitrate_kbps=round(int(bitrate) / 1000, 1) if bitrate and str(bitrate).isdigit() else None,
        width=width,
        height=height,
        rotation=rotation,
        display_width=dw,
        display_height=dh,
        fps=round(fps, 3),
        variable_frame_rate=vfr,
        video_codec=str(video.get("codec_name", "unknown")),
        pixel_format=video.get("pix_fmt"),
        has_audio=audio is not None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        audio_channels=int(audio["channels"]) if audio and audio.get("channels") else None,
    )


# --------------------------------------------------------------------------------------------- proxy


def proxy_dimensions(info: MediaInfo, short_edge: int = PROXY_SHORT_EDGE) -> tuple[int, int]:
    """Display-oriented proxy size with the short edge at ``short_edge`` (never upscaled, even dims)."""
    w, h = info.display_width, info.display_height
    scale = min(1.0, short_edge / min(w, h))
    pw, ph = round(w * scale / 2) * 2, round(h * scale / 2) * 2
    return max(pw, 2), max(ph, 2)


def make_proxy(src: Path, dst: Path, info: MediaInfo, *, timeout: float = 1800.0) -> Path:
    """Create a low-res, constant-frame-rate, display-oriented H.264 proxy for analysis and preview.

    The original is kept untouched for the final render.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    pw, ph = proxy_dimensions(info)
    fps = min(info.fps, PROXY_MAX_FPS)
    tmp = dst.with_suffix(".partial.mp4")
    cmd = [
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        *FF_DECODE, "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        # ffmpeg auto-applies rotation metadata, so the proxy is stored upright.
        "-vf", f"fps={fps:.3f},scale={pw}:{ph}:flags=bicubic,setsar=1,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", *FF_X264,
        "-g", str(max(1, round(fps * 2))), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "48000",
        "-movflags", "+faststart",
        "-metadata:s:v:0", "rotate=0",
        str(tmp),
    ]  # fmt: skip
    _run(cmd, timeout=timeout, what="proxy")
    tmp.replace(dst)
    return dst


# --------------------------------------------------------------------------------------- extraction


@dataclass(frozen=True)
class FrameSpec:
    fps: float
    width: int
    height: int


def frame_spec(info: MediaInfo, fps: float, width: int) -> FrameSpec:
    w = min(width, info.display_width)
    w -= w % 2
    h = round(w * info.display_height / info.display_width / 2) * 2
    return FrameSpec(fps=fps, width=w, height=max(h, 2))


def iter_frames(
    path: Path, spec: FrameSpec, *, gray: bool = False
) -> Iterator[tuple[float, npt.NDArray[np.uint8]]]:
    """Decode frames at a fixed sample rate via an ffmpeg pipe. Yields ``(timestamp_s, frame)``.

    Frames are RGB ``(h, w, 3)`` or grayscale ``(h, w)``. One sequential decode is far faster than
    seeking with OpenCV, and the ``fps`` filter gives exact, evenly spaced timestamps.
    """
    pix, channels = ("gray", 1) if gray else ("rgb24", 3)
    cmd = [
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
        *FF_DECODE, "-i", str(path),
        "-an", "-sn",
        "-vf", f"fps={spec.fps},scale={spec.width}:{spec.height}:flags=area",
        "-f", "rawvideo", "-pix_fmt", pix, "pipe:1",
    ]  # fmt: skip
    frame_bytes = spec.width * spec.height * channels
    shape: tuple[int, ...] = (spec.height, spec.width) if gray else (spec.height, spec.width, 3)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_bytes * 4)
    assert proc.stdout is not None
    index = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield index / spec.fps, np.frombuffer(buf, dtype=np.uint8).reshape(shape)
            index += 1
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
        if code not in (0, -13, 255) and index == 0:  # -13/255: we closed the pipe early on purpose
            raise MediaError("decode_failed", f"frame decode failed: {stderr.strip()[-300:]}")


def extract_audio(
    path: Path,
    sample_rate: int = 16000,
    *,
    timeout: float = 600.0,
    start_s: float | None = None,
    duration_s: float | None = None,
) -> npt.NDArray[np.float32]:
    """Decode the first audio track as mono float32 PCM in [-1, 1], optionally only a window of it."""
    window = (["-ss", f"{start_s:.3f}"] if start_s else []) + (
        ["-t", f"{duration_s:.3f}"] if duration_s else []
    )
    proc = _run(
        [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            *window,
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=timeout,
        what="audio_extract",
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()
