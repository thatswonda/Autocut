"""Voiceover with Piper (on-server neural text-to-speech: no per-minute cost, ~200 MB while speaking).

Voices are downloaded once from the rhasspy/piper-voices collection (MIT-licensed code, voices under
their own permissive licenses listed in each model card) and baked into the Docker image.
"""

from __future__ import annotations

import gc
import logging
import os
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES: dict[str, tuple[str, str, str]] = {
    # key: (model name, folder in the collection, label)
    "female_us": ("en_US-lessac-medium", "en/en_US/lessac/medium", "Warm female, American"),
    "male_us": ("en_US-ryan-medium", "en/en_US/ryan/medium", "Clear male, American"),
    "female_uk": ("en_GB-alba-medium", "en/en_GB/alba/medium", "Female, British"),
    "male_uk": ("en_GB-alan-medium", "en/en_GB/alan/medium", "Male, British"),
}
DEFAULT_VOICE = "female_us"


@dataclass(frozen=True)
class SpokenLine:
    t_in: float
    text: str
    path: Path
    duration_s: float


def _models_dir() -> Path:
    default = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "autoedit" / "models"
    return Path(os.environ.get("AUTOEDIT_MODELS_DIR", default)) / "piper"


def ensure_voice(key: str = DEFAULT_VOICE, *, download: bool = True) -> Path | None:
    name, folder, _ = VOICES.get(key, VOICES[DEFAULT_VOICE])
    model = _models_dir() / f"{name}.onnx"
    meta = _models_dir() / f"{name}.onnx.json"
    if model.is_file() and meta.is_file() and model.stat().st_size > 1_000_000:
        return model
    if not download or os.environ.get("AUTOEDIT_OFFLINE") == "1":
        return None
    model.parent.mkdir(parents=True, exist_ok=True)
    try:
        for dst in (meta, model):
            tmp = dst.with_suffix(dst.suffix + ".part")
            with (
                urllib.request.urlopen(f"{_BASE}/{folder}/{dst.name}", timeout=120) as resp,
                tmp.open("wb") as fh,
            ):
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)
            tmp.chmod(0o644)
            tmp.replace(dst)
    except Exception as exc:
        log.warning("voice %s unavailable (%s)", key, exc)
        return None
    return model


def speak(lines: list[tuple[float, str]], out_dir: Path, voice: str = DEFAULT_VOICE) -> list[SpokenLine]:
    """Synthesize each (t_in, text) line to its own WAV file."""
    model = ensure_voice(voice)
    if model is None:
        raise RuntimeError("The voice model is not available on this server")
    from piper import PiperVoice

    pv = PiperVoice.load(str(model))
    out_dir.mkdir(parents=True, exist_ok=True)
    spoken: list[SpokenLine] = []
    try:
        for i, (t_in, text) in enumerate(lines):
            path = out_dir / f"line{i:02d}.wav"
            with wave.open(str(path), "wb") as w:
                pv.synthesize_wav(text, w)
            with wave.open(str(path), "rb") as r:
                dur = r.getnframes() / float(r.getframerate() or 1)
            spoken.append(SpokenLine(t_in=t_in, text=text, path=path, duration_s=dur))
    finally:
        del pv
        gc.collect()
    return spoken
