"""Sound effects synthesised on the server (no stock library, no licensing questions).

Each effect is designed like a sound designer would layer it: filtered noise for air, pitched sine
bodies with fast decays for hits, harmonic partials for bells. Rendered once per process to WAV.
"""

from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

SR = 48000
KINDS = ("whoosh", "swoosh", "impact", "boom", "pop", "click", "ding", "glitch", "riser", "shutter", "swipe")
Arr = npt.NDArray[np.float64]


def _t(dur: float) -> Arr:
    return np.arange(int(SR * dur)) / SR


def _noise(n: int, seed: int) -> Arr:
    return np.random.default_rng(seed).standard_normal(n)


def _bandsweep(x: Arr, f0: float, f1: float, q: float = 1.2) -> Arr:
    """Time-varying band-pass (state-variable filter) sweeping f0 -> f1."""
    n = len(x)
    f = np.geomspace(f0, f1, n)
    g = np.tan(np.pi * f / SR)
    r = 1 / q
    lp = bp = 0.0
    out = np.empty(n)
    for i in range(n):  # small buffers (< 1.5 s); clarity over speed
        hp = (x[i] - (r + g[i]) * bp - lp) / (1 + r * g[i] + g[i] * g[i])
        bp_new = g[i] * hp + bp
        lp = g[i] * bp_new + lp
        bp = g[i] * bp_new + bp_new
        out[i] = bp_new
    return out


def _env(t: Arr, attack: float, decay: float) -> Arr:
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(t - attack, 0) / max(decay, 1e-4))


def synth(kind: str) -> Arr:
    if kind == "whoosh":
        t = _t(0.55)
        x = _bandsweep(_noise(len(t), 1), 300, 4200, 1.4) * np.sin(np.pi * np.clip(t / 0.55, 0, 1)) ** 2
        return x * 1.6
    if kind == "swoosh":
        t = _t(0.32)
        x = _bandsweep(_noise(len(t), 2), 2500, 600, 1.8) * np.sin(np.pi * t / 0.32) ** 1.5
        return x * 1.8
    if kind == "swipe":
        t = _t(0.22)
        return _bandsweep(_noise(len(t), 3), 900, 6000, 2.2) * np.sin(np.pi * t / 0.22) * 1.5
    if kind == "impact":
        t = _t(0.7)
        f = 95 * np.exp(-t * 9) + 48
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t, 0.002, 0.16)
        click = _noise(len(t), 4) * _env(t, 0.0005, 0.012) * 0.6
        return body * 0.95 + click
    if kind == "boom":
        t = _t(1.4)
        f = 70 * np.exp(-t * 3) + 32
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t, 0.004, 0.45)
        air = _bandsweep(_noise(len(t), 5), 1800, 200, 0.8) * _env(t, 0.003, 0.25) * 0.35
        return body + air
    if kind == "pop":
        t = _t(0.12)
        f = 900 * np.exp(-t * 30) + 380
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(t, 0.001, 0.035) * 0.8
    if kind == "click":
        t = _t(0.04)
        return (_noise(len(t), 6) * 0.4 + np.sin(2 * np.pi * 2400 * t)) * _env(t, 0.0003, 0.006) * 0.7
    if kind == "ding":
        t = _t(1.2)
        x = sum(a * np.sin(2 * np.pi * 880 * m * t) * np.exp(-t * d) for m, a, d in
                ((1, 1.0, 3.2), (2.76, 0.45, 5.0), (5.4, 0.22, 8.0), (8.93, 0.1, 11.0)))  # fmt: skip
        return x * _env(t, 0.002, 10) * 0.45
    if kind == "glitch":
        t = _t(0.3)
        n = _noise(len(t), 7)
        steps = np.repeat(np.random.default_rng(8).uniform(-1, 1, len(t) // 400 + 1), 400)[: len(t)]
        crushed = np.round(n * 3) / 3
        gate = (np.sin(2 * np.pi * 37 * t) > -0.2).astype(float)
        return (
            (crushed * 0.35 + steps * 0.5 + np.sign(np.sin(2 * np.pi * 180 * t)) * 0.15)
            * gate
            * _env(t, 0.002, 0.2)
        )
    if kind == "riser":
        t = _t(1.6)
        f = np.geomspace(180, 1400, len(t))
        tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.25
        air = _bandsweep(_noise(len(t), 9), 400, 7000, 1.2) * 0.8
        return (tone + air) * (t / 1.6) ** 2
    if kind == "shutter":
        t = _t(0.18)
        a = _noise(len(t), 10) * _env(t, 0.0005, 0.01)
        b = np.roll(_noise(len(t), 11) * _env(t, 0.0005, 0.015), int(0.07 * SR)) * 0.8
        return (a + b) * 0.8
    raise ValueError(f"unknown sound effect {kind}")


def _cache_dir() -> Path:
    p = Path(os.environ.get("AUTOEDIT_SFX_DIR") or Path(tempfile.gettempdir()) / "autoedit-sfx")
    p.mkdir(parents=True, exist_ok=True)
    return p


def sfx_file(kind: str) -> Path:
    """WAV (48 kHz stereo, peak -3 dBFS) for ``kind``, synthesised once and cached."""
    path = _cache_dir() / f"{kind}.v1.wav"
    if path.is_file():
        return path
    x = synth(kind)
    x = x / max(1e-6, float(np.max(np.abs(x)))) * 0.707
    fade = min(len(x) // 4, int(0.004 * SR))
    x[-fade:] *= np.linspace(1, 0, fade)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    tmp = path.with_suffix(".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())
    tmp.replace(path)
    return path
