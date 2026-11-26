"""Audio signals: EBU R128 loudness, silence, speech (VAD), music, and beats."""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from ..media import FFMPEG, MediaError, extract_audio
from ..models import model_path
from ..native import quiet_native_stderr
from .ranges import mask_to_ranges

log = logging.getLogger(__name__)

SR = 16000
VAD_FRAME_S = 0.03
VAD_AGGRESSIVENESS = 2
LOUDNESS_STEP_S = 0.1
SILENCE_FLOOR_LUFS = -45.0  # absolute: quieter than this is silence
SILENCE_RELATIVE_LU = 20.0  # relative: this far below the clip's integrated loudness is silence
MIN_SILENCE_S = 0.5
SPEECH_GATE = 0.2  # YAMNet speech probability that lets VAD frames through
MUSIC_THRESHOLD = 0.3
MIN_MUSIC_S = 2.0
YAMNET_WINDOW_S = 0.975

_SPEECH_LABELS = {
    "Speech",
    "Male speech, man speaking",
    "Female speech, woman speaking",
    "Child speech, kid speaking",
    "Conversation",
    "Narration, monologue",
    "Speech synthesizer",
    "Whispering",
}
_MUSIC_LABELS = {"Music", "Singing", "Musical instrument"}


@dataclass
class AudioAnalysis:
    duration_s: float
    integrated_lufs: float | None
    loudness_range_lu: float | None
    peak_dbfs: float | None
    loudness_times: npt.NDArray[np.float64]  # centre of each 400 ms momentary window
    loudness: npt.NDArray[np.float64]  # momentary LUFS
    silence_threshold_lufs: float
    silences: list[tuple[float, float]]
    speech_frames: npt.NDArray[np.bool_]  # one per VAD_FRAME_S
    speech_ranges: list[tuple[float, float]]
    music_ranges: list[tuple[float, float]]
    beats: list[float]
    tempo_bpm: float | None
    detectors: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------------------------------ loudness

_M_LINE = re.compile(rb"pts_time:([0-9.]+)\s+lavfi\.r128\.M=(-?[0-9.]+|-?inf|nan)", re.S)


def measure_loudness(path: Path, timeout: float = 600.0) -> tuple[
    npt.NDArray[np.float64], npt.NDArray[np.float64], float | None, float | None, float | None
]:  # fmt: skip
    """Run ffmpeg's EBU R128 meter. Returns (times, momentary LUFS, integrated, LRA, sample peak)."""
    cmd = [
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", "-i", str(path),
        "-map", "0:a:0", "-vn",
        "-af", "ebur128=metadata=1:peak=sample,ametadata=mode=print:key=lavfi.r128.M:file=/dev/stdout",
        "-f", "null", "-",
    ]  # fmt: skip
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("timeout", "loudness measurement timed out") from exc
    if proc.returncode != 0:
        raise MediaError("loudness_failed", proc.stderr.decode("utf-8", "replace")[-300:])
    pairs = _M_LINE.findall(proc.stdout.replace(b"\n", b" "))
    times = np.array([float(t) for t, _ in pairs], dtype=np.float64)
    values = np.array([float(v) if v not in (b"nan",) else -120.0 for _, v in pairs], dtype=np.float64)
    values = np.clip(np.nan_to_num(values, neginf=-120.0, posinf=0.0), -120.0, 0.0)
    # The momentary value reported at t covers the 400 ms window ending at t.
    times = np.clip(times - 0.2, 0.0, None)

    summary = proc.stderr.decode("utf-8", "replace").rsplit("Summary:", 1)[-1]

    def grab(label: str) -> float | None:
        m = re.search(rf"{label}:\s+(-?[0-9.]+|-inf)", summary)
        if not m or m.group(1) == "-inf":
            return None
        val = float(m.group(1))
        return None if val <= -70.0 and label == "I" else val

    return times, values, grab("I"), grab("LRA"), grab("Peak")


# ----------------------------------------------------------------------------------------- classify


def _yamnet_windows(samples: npt.NDArray[np.float32]) -> tuple[list[float], list[float]] | None:
    """Speech and music probability per ~0.975 s YAMNet window, or ``None`` if unavailable."""
    path = model_path("audio")
    if path is None:
        return None
    try:
        from mediapipe.tasks.python import BaseOptions, audio
        from mediapipe.tasks.python.components.containers import audio_data
    except Exception as exc:  # pragma: no cover - mediapipe missing
        log.warning("mediapipe audio unavailable: %s", exc)
        return None
    classifier: Any = None
    try:
        with quiet_native_stderr():
            classifier = audio.AudioClassifier.create_from_options(
                audio.AudioClassifierOptions(
                    base_options=BaseOptions(model_asset_path=str(path)),
                    running_mode=audio.RunningMode.AUDIO_CLIPS,
                    max_results=-1,
                )
            )
        results = classifier.classify(audio_data.AudioData.create_from_array(samples, SR))
    except Exception as exc:
        log.warning("YAMNet classification failed (%s); speech falls back to VAD only", exc)
        return None
    finally:
        if classifier is not None:
            with contextlib.suppress(Exception), quiet_native_stderr():
                classifier.close()
    speech: list[float] = []
    music: list[float] = []
    for r in results:
        cats = {c.category_name: float(c.score) for c in r.classifications[0].categories}
        speech.append(max((cats.get(k, 0.0) for k in _SPEECH_LABELS), default=0.0))
        music.append(max((cats.get(k, 0.0) for k in _MUSIC_LABELS), default=0.0))
    return speech, music


def _vad_frames(samples: npt.NDArray[np.float32]) -> npt.NDArray[np.bool_]:
    import webrtcvad

    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    n = int(SR * VAD_FRAME_S)
    count = len(pcm) // n
    out = np.zeros(count, dtype=bool)
    for i in range(count):
        out[i] = vad.is_speech(pcm[i * n : (i + 1) * n].tobytes(), SR)
    return out


def _window_values_at(
    times: npt.NDArray[np.floating[Any]], window_values: list[float], reach_s: float = 0.3
) -> npt.NDArray[np.float64]:
    """Max classifier score among windows within ``reach_s`` of each time.

    The small reach keeps speech onsets at window edges without leaking a whole window into music.
    """
    if not window_values:
        return np.zeros_like(times)
    vals = np.asarray(window_values)
    last = len(window_values) - 1
    out = np.zeros_like(times)
    for offset in (-reach_s, 0.0, reach_s):
        idx = np.clip(((times + offset) / YAMNET_WINDOW_S).astype(int), 0, last)
        out = np.maximum(out, vals[idx])
    return np.asarray(out, dtype=np.float64)


# ------------------------------------------------------------------------------------------ analyse


def analyse_audio(path: Path, duration_s: float) -> AudioAnalysis:
    detectors = {"loudness": "ffmpeg.ebur128", "vad": "webrtcvad"}
    lt, lm, integrated, lra, peak = measure_loudness(path)
    threshold = SILENCE_FLOOR_LUFS
    if integrated is not None:
        threshold = min(
            SILENCE_FLOOR_LUFS + 10.0, max(SILENCE_FLOOR_LUFS - 10.0, integrated - SILENCE_RELATIVE_LU)
        )
    silent_mask = lm < threshold
    silences = mask_to_ranges(
        silent_mask, LOUDNESS_STEP_S, min_len_s=MIN_SILENCE_S, merge_gap_s=0.15, end_s=duration_s
    )

    samples = extract_audio(path, SR)
    vad = _vad_frames(samples)
    frame_times = (np.arange(vad.size) + 0.5) * VAD_FRAME_S

    windows = _yamnet_windows(samples)
    music_ranges: list[tuple[float, float]] = []
    if windows is not None:
        speech_p, music_p = windows
        detectors["speech_music"] = "mediapipe.yamnet"
        sp_at = _window_values_at(frame_times, speech_p)
        mu_at = _window_values_at(frame_times, music_p, reach_s=0.0)
        # VAD fires on music; inside music only trust frames where the classifier hears speech too.
        gate = (sp_at >= SPEECH_GATE) & ((mu_at < MUSIC_THRESHOLD) | (sp_at >= 0.5 * mu_at))
        speech = vad & gate
        music_mask = np.asarray(music_p) >= MUSIC_THRESHOLD
        music_ranges = mask_to_ranges(
            music_mask, YAMNET_WINDOW_S, min_len_s=MIN_MUSIC_S, merge_gap_s=1.0, end_s=duration_s
        )
    else:
        detectors["speech_music"] = "webrtcvad-only"
        speech = vad

    # Never call quiet frames speech.
    if lm.size:
        loud_at = np.interp(frame_times, lt, lm)
        speech &= loud_at >= threshold

    speech_ranges = mask_to_ranges(speech, VAD_FRAME_S, min_len_s=0.25, merge_gap_s=0.3, end_s=duration_s)

    beats: list[float] = []
    tempo: float | None = None
    if music_ranges and samples.size > SR * 2:
        import librosa

        detectors["beats"] = "librosa.beat_track"
        tempo_arr, beat_times = librosa.beat.beat_track(y=samples, sr=SR, units="time")
        tempo = round(float(np.atleast_1d(tempo_arr)[0]), 1) or None
        beats = [round(float(b), 3) for b in beat_times if any(a <= b <= z for a, z in music_ranges)]

    return AudioAnalysis(
        duration_s=duration_s,
        integrated_lufs=integrated,
        loudness_range_lu=lra,
        peak_dbfs=peak,
        loudness_times=lt,
        loudness=lm,
        silence_threshold_lufs=round(threshold, 1),
        silences=silences,
        speech_frames=speech,
        speech_ranges=speech_ranges,
        music_ranges=music_ranges,
        beats=beats,
        tempo_bpm=tempo,
        detectors=detectors,
    )
