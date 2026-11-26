"""On-device ML models (MediaPipe Tasks). Downloaded once, cached, and baked into the Docker image."""

from __future__ import annotations

import logging
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_BASE = "https://storage.googleapis.com/mediapipe-models"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    filename: str
    url: str
    min_bytes: int
    purpose: str


MODELS: dict[str, ModelSpec] = {
    "face": ModelSpec(
        "face",
        "blaze_face_short_range.tflite",
        f"{_BASE}/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
        100_000,
        "face boxes for smart cropping (framing only, never identification)",
    ),
    "pose": ModelSpec(
        "pose",
        "pose_landmarker_lite.task",
        f"{_BASE}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
        1_000_000,
        "person presence and body box",
    ),
    "audio": ModelSpec(
        "audio",
        "yamnet.tflite",
        f"{_BASE}/audio_classifier/yamnet/float32/1/yamnet.tflite",
        1_000_000,
        "speech / music / silence classification (AudioSet YAMNet)",
    ),
}


def models_dir() -> Path:
    default = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "autoedit" / "models"
    return Path(os.environ.get("AUTOEDIT_MODELS_DIR", default))


def model_path(name: str, *, download: bool = True) -> Path | None:
    """Return the local path of a model, downloading it if allowed. ``None`` if unavailable.

    Callers fall back to classical detectors when this returns ``None``, so a missing model degrades
    quality but never fails a job.
    """
    spec = MODELS[name]
    path = models_dir() / spec.filename
    if path.is_file() and path.stat().st_size >= spec.min_bytes:
        if os.access(path, os.R_OK):
            return path
        log.warning("model %s exists but is not readable by this user; using fallback detector", path)
        return None
    if not download or os.environ.get("AUTOEDIT_OFFLINE") == "1":
        return None
    try:
        fetch(spec, path)
    except Exception as exc:
        log.warning("model %s unavailable (%s); using fallback detector", name, exc)
        return None
    return path


def fetch(spec: ModelSpec, path: Path, timeout: float = 60.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".part") as tmp:
        tmp_path = Path(tmp.name)
        with urllib.request.urlopen(spec.url, timeout=timeout) as resp:
            while chunk := resp.read(1 << 20):
                tmp.write(chunk)
    if tmp_path.stat().st_size < spec.min_bytes:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"download of {spec.filename} looks truncated")
    # Temp files are created 0600; models are shared read-only data (e.g. baked into an image as root
    # and read by an unprivileged worker), so make them world-readable.
    tmp_path.chmod(0o644)
    tmp_path.replace(path)


def download_all() -> dict[str, Path | None]:
    return {name: model_path(name) for name in MODELS}
