"""Background music beds generated on the server (no stock library, nothing to license).

A bed is a four-chord loop arranged like a producer would for an advert: warm detuned pads, a bass that
follows the root, drums for the energetic moods (kick, clap, hats), a plucked arpeggio with an echo, and
side-chain "pumping" so the pads breathe with the kick. Moods set tempo, key, instruments and density.
Rendered to 48 kHz stereo WAV, faded in and out, peaking at -1 dBFS.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from scipy.signal import butter, lfilter

SR = 48000
Arr = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Mood:
    bpm: float
    root: int  # MIDI note of the key's tonic
    chords: tuple[tuple[int, ...], ...]  # semitone offsets from the root, one chord per bar
    drums: str  # none | soft | full
    arp: bool
    pad_cutoff: float
    bass_eighths: bool


MOODS: dict[str, Mood] = {
    # I - V - vi - IV in A, bright and driving
    "upbeat": Mood(118, 57, ((0, 4, 7, 11), (7, 11, 14, 17), (9, 12, 16, 19), (5, 9, 12, 16)), "full", True, 2600, True),
    # vi - IV - I - V, steady and confident
    "corporate": Mood(104, 55, ((9, 12, 16, 19), (5, 9, 12, 16), (0, 4, 7, 11), (7, 11, 14, 17)), "soft", True, 2200, False),
    # ii - V - I - vi with sevenths, laid back
    "chill": Mood(86, 53, ((2, 5, 9, 12), (7, 11, 14, 17), (0, 4, 7, 11), (9, 12, 16, 19)), "soft", True, 1500, False),
    # i - VI - III - VII in minor, slow and elegant
    "luxury": Mood(74, 50, ((0, 3, 7, 10), (8, 12, 15, 19), (3, 7, 10, 14), (10, 14, 17, 21)), "none", False, 1300, False),
}  # fmt: skip


def mood_for_style(style: str) -> str:
    return {"bold_pop": "upbeat", "tech_grid": "upbeat", "warm_friendly": "chill", "clean_minimal": "corporate",
            "editorial": "corporate", "luxury": "luxury"}.get(style, "corporate")  # fmt: skip


def _hz(midi: float) -> float:
    return float(440.0 * 2 ** ((midi - 69) / 12))


def _lp(x: Arr, cutoff: float, order: int = 2) -> Arr:
    b, a = butter(order, min(0.99, cutoff / (SR / 2)), "low")
    return np.asarray(lfilter(b, a, x), dtype=np.float64)


def _hp(x: Arr, cutoff: float) -> Arr:
    b, a = butter(2, min(0.99, cutoff / (SR / 2)), "high")
    return np.asarray(lfilter(b, a, x), dtype=np.float64)


def _saw(f: float, n: int, phase: float = 0.0) -> Arr:
    t = np.arange(n) / SR
    return 2 * ((t * f + phase) % 1.0) - 1


def render_bed(mood_key: str, seconds: float, *, seed: int = 0) -> Arr:
    """Stereo float array (n, 2) of about ``seconds`` of music."""
    m = MOODS.get(mood_key, MOODS["corporate"])
    rng = np.random.default_rng(seed)
    beat = 60.0 / m.bpm
    bar = beat * 4
    n_bars = int(np.ceil((seconds + 1.0) / bar))
    n = int(n_bars * bar * SR)
    left = np.zeros(n)
    right = np.zeros(n)
    bar_n = int(bar * SR)

    # Pads: three detuned saws per note, slow swell per bar, low-passed.
    for i in range(n_bars):
        chord = m.chords[i % len(m.chords)]
        a, b = i * bar_n, min(n, (i + 1) * bar_n + int(0.3 * SR))
        seg = b - a
        env = np.minimum(1, np.arange(seg) / (0.35 * SR)) * np.minimum(1, (seg - np.arange(seg)) / (0.4 * SR))
        for note in chord:
            f = _hz(m.root + note)
            for det, pan in ((-0.08, 0.2), (0.0, 0.5), (0.08, 0.8)):
                tone = _saw(f * 2 ** (det / 12), seg, rng.random()) * env * 0.05
                left[a:b] += tone * (1 - pan)
                right[a:b] += tone * pan
    left = _lp(left, m.pad_cutoff)
    right = _lp(right, m.pad_cutoff)

    # Side-chain envelope from the kick grid (used even without drums for a gentle breath).
    pump = np.ones(n)
    for k in range(int(n / (beat * SR))):
        s = int(k * beat * SR)
        e = min(n, s + int(beat * SR))
        x = np.arange(e - s) / SR
        pump[s:e] = 1 - (0.55 if m.drums == "full" else 0.25) * np.exp(-x / 0.12)
    left *= pump
    right *= pump

    # Bass: root of each chord, plucked on beats (or eighths).
    bass = np.zeros(n)
    step = beat / 2 if m.bass_eighths else beat
    for k in range(int(n / (step * SR))):
        s = int(k * step * SR)
        bar_i = int((k * step) // bar)
        root = m.root - 24 + m.chords[bar_i % len(m.chords)][0]
        seg = min(n - s, int(step * SR))
        x = np.arange(seg) / SR
        f = _hz(root)
        tone = np.sin(2 * np.pi * f * x) + 0.3 * np.sin(4 * np.pi * f * x)
        bass[s : s + seg] += np.tanh(1.6 * tone) * np.exp(-x / (step * 0.9)) * 0.22
    bass = _lp(bass, 900)
    left += bass * pump
    right += bass * pump

    # Drums.
    if m.drums != "none":
        for k in range(int(n / (beat * SR))):
            s = int(k * beat * SR)
            seg = min(n - s, int(0.35 * SR))
            x = np.arange(seg) / SR
            kick = np.sin(2 * np.pi * np.cumsum(50 + 110 * np.exp(-x * 28)) / SR) * np.exp(-x * 9) * 0.55
            left[s : s + seg] += kick
            right[s : s + seg] += kick
            if m.drums == "full" and k % 4 in (1, 3):
                cl = _hp(rng.standard_normal(seg), 900) * np.exp(-x * 22) * 0.22
                left[s : s + seg] += cl
                right[s : s + seg] += cl
        hat_step = beat / (4 if m.drums == "full" else 2)
        for k in range(int(n / (hat_step * SR))):
            s = int(k * hat_step * SR)
            seg = min(n - s, int(0.06 * SR))
            x = np.arange(seg) / SR
            vel = 0.06 if k % 2 == 0 else 0.035
            h = _hp(rng.standard_normal(seg), 7000) * np.exp(-x * 70) * vel
            left[s : s + seg] += h * 0.8
            right[s : s + seg] += h * 1.1

    # Plucked arpeggio with a dotted-eighth echo.
    if m.arp:
        arp = np.zeros(n)
        st = beat / 4 if m.drums == "full" else beat / 2
        for k in range(int(n / (st * SR))):
            s = int(k * st * SR)
            bar_i = int((k * st) // bar)
            chord = m.chords[bar_i % len(m.chords)]
            note = m.root + 12 + chord[(k * 3) % len(chord)]
            seg = min(n - s, int(0.4 * SR))
            x = np.arange(seg) / SR
            f = _hz(note)
            tone = (np.sin(2 * np.pi * f * x) + 0.25 * np.sin(6 * np.pi * f * x)) * np.exp(-x * 11)
            arp[s : s + seg] += tone * 0.07
        delay = int(beat * 0.75 * SR)
        echo = np.zeros(n)
        echo[delay:] = arp[:-delay] * 0.35
        left += arp + echo * 0.6
        right += arp * 0.8 + echo

    out = np.stack([left, right], axis=1)
    keep = int(seconds * SR)
    out = out[:keep] if keep < len(out) else out
    fade_in, fade_out = int(0.4 * SR), int(min(2.0, seconds / 4) * SR)
    out[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    out[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    out = np.tanh(out * 1.2)
    return out / max(1e-6, float(np.max(np.abs(out)))) * 0.89


def bed_file(mood_key: str, seconds: float) -> Path:
    """A cached WAV bed for the mood and length."""
    seconds = round(max(3.0, seconds), 1)
    digest = hashlib.sha1(f"{mood_key}:{seconds}:v1".encode()).hexdigest()[:10]
    root = Path(os.environ.get("AUTOEDIT_SFX_DIR") or Path(tempfile.gettempdir()) / "autoedit-sfx")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"bed-{mood_key}-{digest}.wav"
    if path.is_file():
        return path
    pcm = (np.clip(render_bed(mood_key, seconds), -1, 1) * 32767).astype("<i2")
    tmp = path.with_suffix(".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)
    return path
