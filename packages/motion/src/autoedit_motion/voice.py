"""Voiceover: Google Gemini text-to-speech when GEMINI_API_KEY is set (natural, directable voices:
tone and accent are asked for in plain words), else Piper on the server (no key, no per-minute cost).

Gemini is called over REST (``models/{model}:generateContent`` with an AUDIO response; raw 16-bit PCM,
24 kHz mono comes back). The model is ``GEMINI_TTS_MODEL``; if Google rejects a model name the next
known one is tried. Any Gemini failure falls back to Piper for the whole voiceover, and the caller is
told why so the app can say so.

Piper voices are downloaded once from the rhasspy/piper-voices collection and baked into the image.
"""

from __future__ import annotations

import base64
import gc
import logging
import os
import time
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS = ("gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts")
# Prebuilt Gemini voices, with the character Google gives each.
GEMINI_VOICES: dict[str, str] = {
    "Kore": "Firm", "Puck": "Upbeat", "Charon": "Informative", "Zephyr": "Bright", "Fenrir": "Excitable",
    "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy", "Callirrhoe": "Easy-going", "Autonoe": "Bright",
    "Enceladus": "Breathy", "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth",
    "Despina": "Smooth", "Erinome": "Clear", "Algenib": "Gravelly", "Rasalgethi": "Informative",
    "Laomedeia": "Upbeat", "Achernar": "Soft", "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature",
    "Pulcherrima": "Forward", "Achird": "Friendly", "Zubenelgenubi": "Casual", "Vindemiatrix": "Gentle",
    "Sadachbia": "Lively", "Sadaltager": "Knowledgeable", "Sulafat": "Warm",
}  # fmt: skip
DEFAULT_GEMINI_VOICE = "Kore"
DEFAULT_STYLE = "Read this like a confident, warm, upbeat voiceover for a premium product advert."


class VoiceError(RuntimeError):
    pass


def gemini_key() -> str | None:
    raw = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    key = raw.strip().strip("\"'").strip()
    return key or None


def gemini_available() -> bool:
    return gemini_key() is not None


def _gemini_line(client: Any, text: str, voice: str, style: str, key: str) -> bytes:
    """One line to raw PCM (s16le, 24 kHz, mono) via Gemini. Tries the known models in turn."""
    models = [m for m in dict.fromkeys([os.environ.get("GEMINI_TTS_MODEL", ""), *GEMINI_MODELS]) if m]
    prompt = f"{style.strip()} Say it naturally, without adding any words:\n{text.strip()}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    last = ""
    for model in models:
        for attempt in range(4):
            resp = client.post(GEMINI_URL.format(model=model), json=body,
                               headers={"x-goog-api-key": key, "Content-Type": "application/json"})  # fmt: skip
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = float(resp.headers.get("retry-after") or 0) or (2**attempt) * 3
                last = f"Gemini is busy ({resp.status_code})"
                time.sleep(min(wait, 30))
                continue
            if resp.status_code in (400, 404) and "model" in resp.text.lower():
                last = f"model {model} not available"
                break  # try the next model
            if resp.status_code in (401, 403):
                raise VoiceError("The Gemini API key was rejected. Check GEMINI_API_KEY.")
            if resp.status_code != 200:
                raise VoiceError(f"Gemini voice failed ({resp.status_code}): {resp.text[:160]}")
            try:
                parts = resp.json()["candidates"][0]["content"]["parts"]
                data = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
            except (KeyError, IndexError, StopIteration, ValueError) as exc:
                raise VoiceError("Gemini returned no audio") from exc
            return base64.b64decode(data)
    raise VoiceError(f"Gemini voice unavailable: {last}")


def speak_gemini(lines: list[tuple[float, str]], out_dir: Path, *, voice: str = DEFAULT_GEMINI_VOICE,
                 style: str = DEFAULT_STYLE) -> list[SpokenLine]:  # fmt: skip
    import httpx

    key = gemini_key()
    if key is None:
        raise VoiceError("GEMINI_API_KEY is not set")
    voice = voice if voice in GEMINI_VOICES else DEFAULT_GEMINI_VOICE
    out_dir.mkdir(parents=True, exist_ok=True)
    spoken: list[SpokenLine] = []
    with httpx.Client(timeout=90.0) as client:
        for i, (t_in, text) in enumerate(lines):
            pcm = _gemini_line(client, text, voice, style or DEFAULT_STYLE, key)
            path = out_dir / f"line{i:02d}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
            spoken.append(SpokenLine(t_in=t_in, text=text, path=path, duration_s=len(pcm) / 2 / 24000))
    return spoken


def speak_best(
    lines: list[tuple[float, str]], out_dir: Path, *, voice: str = "", style: str = ""
) -> tuple[list[SpokenLine], str]:
    """Gemini when configured and the voice is a Gemini voice (or unset), else Piper. Returns the lines and a
    note for the user when Gemini was wanted but could not be used."""
    wants_gemini = gemini_available() and (voice in GEMINI_VOICES or voice in ("", "auto"))
    if wants_gemini:
        try:
            return speak_gemini(lines, out_dir, voice=voice or DEFAULT_GEMINI_VOICE, style=style), ""
        except Exception as exc:  # never lose the video over the voice
            log.warning("Gemini voiceover failed, using the server voice: %s", exc)
            note = f"Gemini voice failed ({exc}); used the built-in voice instead."
            return speak(lines, out_dir / "piper", voice if voice in VOICES else DEFAULT_VOICE), note
    return speak(lines, out_dir, voice if voice in VOICES else DEFAULT_VOICE), ""


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
