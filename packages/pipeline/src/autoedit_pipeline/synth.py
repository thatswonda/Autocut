"""Synthesise test clips with known ground truth (used by the test suite and the eval smoke set).

The default "storyboard" clip (30 s, 1280x720, 30 fps):

======  ==========================  ===============  =======================================
t (s)   picture                     sound            expected signals
======  ==========================  ===============  =======================================
0-3     black                       silence          dead: black_frames, silence
3-10    moving test pattern         speech           speech, not dead
10-14   static colour bars          silence          dead: static_empty, silence, scene cut
14-24   moving test pattern         music            music, beats
24-30   static bars, hand-held      speech           shake flag, speech, not dead
======  ==========================  ===============  =======================================
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

from .media import FFMPEG

SR = 48000
SPEECH_A = (
    "Hi everyone, welcome back to the channel. Today I am going to show you how to make the perfect "
    "cup of coffee at home."
)
SPEECH_B = "So that is it. Thanks for watching, and see you in the next video."


def _speech(text: str, seconds: float) -> npt.NDArray[np.float32]:
    """Synthesised speech via espeak-ng, or a speech-like formant babble if it is not installed."""
    n = int(seconds * SR)
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if espeak:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "s.wav"
            subprocess.run([espeak, "-s", "165", "-w", str(raw), text], check=True, capture_output=True)
            proc = subprocess.run(
                [
                    FFMPEG,
                    "-loglevel",
                    "error",
                    "-i",
                    str(raw),
                    "-ac",
                    "1",
                    "-ar",
                    str(SR),
                    "-f",
                    "f32le",
                    "-",
                ],
                check=True,
                capture_output=True,
            )
            y = np.frombuffer(proc.stdout, dtype=np.float32)
    else:  # pragma: no cover - CI installs espeak-ng
        t = np.arange(n) / SR
        syll = (np.sin(2 * np.pi * 4.0 * t) > 0).astype(np.float32)
        f0 = 140 + 30 * np.sin(2 * np.pi * 0.7 * t)
        y = (np.sin(2 * np.pi * np.cumsum(f0) / SR) * syll * 0.3).astype(np.float32)
    out = np.zeros(n, dtype=np.float32)
    y = y[:n]
    out[: y.size] = y / (np.abs(y).max() + 1e-9) * 0.5
    return out


def _music(seconds: float, bpm: float = 120.0) -> npt.NDArray[np.float32]:
    """Drums, bass and chords at a steady tempo."""
    rng = np.random.default_rng(7)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    beat = 60.0 / bpm
    y = np.zeros(n, dtype=np.float64)
    chords = [
        (220.0, 277.18, 329.63),
        (196.0, 246.94, 293.66),
        (174.61, 220.0, 261.63),
        (196.0, 246.94, 311.13),
    ]
    for i in range(int(seconds / beat)):
        start = int(i * beat * SR)
        # kick
        k = np.arange(min(int(0.25 * SR), n - start)) / SR
        y[start : start + k.size] += (
            0.9 * np.sin(2 * np.pi * (50 + 90 * np.exp(-k * 30)) * k) * np.exp(-k * 12)
        )
        # hat on the off-beat
        hs = start + int(beat / 2 * SR)
        h = min(int(0.05 * SR), max(0, n - hs))
        y[hs : hs + h] += 0.15 * rng.standard_normal(h) * np.exp(-np.arange(h) / SR * 60)
    for bar in range(int(seconds / (beat * 4)) + 1):
        a, b = int(bar * 4 * beat * SR), min(n, int((bar + 1) * 4 * beat * SR))
        if a >= n:
            break
        seg = t[a:b] - t[a]
        env = np.minimum(1.0, seg * 20) * np.exp(-seg * 0.4)
        for f in chords[bar % len(chords)]:
            y[a:b] += 0.12 * env * (np.sin(2 * np.pi * f * seg) + 0.4 * np.sin(4 * np.pi * f * seg))
        y[a:b] += 0.25 * env * np.sin(2 * np.pi * chords[bar % len(chords)][0] / 2 * seg)
    y /= np.abs(y).max() + 1e-9
    return (y * 0.6).astype(np.float32)


def _write_wav(path: Path, y: npt.NDArray[np.float32]) -> None:
    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def storyboard_audio() -> npt.NDArray[np.float32]:
    y = np.zeros(30 * SR, dtype=np.float32)
    y[3 * SR : 10 * SR] = _speech(SPEECH_A, 7)
    y[14 * SR : 24 * SR] = _music(10)
    y[24 * SR : 30 * SR] = _speech(SPEECH_B, 6)
    # A faint room-noise floor so "silence" is realistic rather than digital zero.
    y += np.random.default_rng(1).standard_normal(y.size).astype(np.float32) * 0.0008
    return y


def make_storyboard_clip(dst: Path, *, width: int = 1280, height: int = 720, rotate: int = 0,
                         audio: bool = True, container: str | None = None) -> Path:  # fmt: skip
    """Render the storyboard clip. ``rotate`` writes rotation metadata (as phones do)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = f"{width}x{height}"
    fc = (
        f"color=c=black:s={size}:r=30:d=3[v0];"
        f"testsrc2=s={size}:r=30:d=7[v1];"
        f"smptebars=s={size}:r=30:d=4[v2];"
        f"testsrc2=s={size}:r=30:d=10,hue=h=90[v3];"
        f"smptebars=s={width + 80}x{height + 80}:r=30:d=6,"
        f"crop={width}:{height}:'40+30*sin(t*31)+12*sin(t*53)':'40+26*sin(t*27)+10*sin(t*61)'[v4];"
        "[v0][v1][v2][v3][v4]concat=n=5:v=1:a=0,format=yuv420p[v]"
    )
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [FFMPEG, "-nostdin", "-loglevel", "error", "-y"]
        if audio:
            wav = Path(tmp) / "a.wav"
            _write_wav(wav, storyboard_audio())
            cmd += ["-i", str(wav), "-filter_complex", fc, "-map", "[v]", "-map", "0:a"]
        else:
            cmd += ["-filter_complex", fc, "-map", "[v]"]
        ext = container or dst.suffix.lstrip(".")
        if ext == "webm":
            vcodec = ["-c:v", "libvpx-vp9", "-b:v", "1M", "-deadline", "realtime", "-cpu-used", "8"]
            acodec = ["-c:a", "libopus"]
        else:
            vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
            acodec = ["-c:a", "aac", "-b:a", "128k"]
        cmd += [*vcodec, *(acodec if audio else []), "-t", "30", str(dst)]
        subprocess.run(cmd, check=True)
    if rotate:
        rotated = dst.with_name(dst.stem + ".rot" + dst.suffix)
        subprocess.run(
            [FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-display_rotation", str(-rotate), "-i", str(dst),
             "-c", "copy", str(rotated)],
            check=True,
        )  # fmt: skip
        rotated.replace(dst)
    return dst
