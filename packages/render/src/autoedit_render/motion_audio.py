"""Sound for motion-graphics videos: voiceover placed on the timeline, music ducked under it, optional
captions timed to the narration, loudness to -14 LUFS. The animation itself is rendered silent."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from autoedit_schemas.edl import CaptionWord

from .captions import subtitles_filter, write_ass

FFMPEG = os.environ.get("AUTOEDIT_FFMPEG", "ffmpeg")
MAX_TEMPO = 1.25


@dataclass(frozen=True)
class Line:
    t_in: float
    text: str
    path: Path
    duration_s: float


def _run(cmd: list[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, timeout=900, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed: {proc.stderr.decode('utf-8', 'replace')[-400:]}")


def fit_lines(lines: list[Line], total_s: float) -> list[tuple[Line, float]]:
    """Tempo per line so it ends before the next one starts (never faster than ``MAX_TEMPO``)."""
    ordered = sorted(lines, key=lambda x: x.t_in)
    out = []
    for i, ln in enumerate(ordered):
        end = ordered[i + 1].t_in - 0.1 if i + 1 < len(ordered) else total_s - 0.2
        room = max(0.3, end - ln.t_in)
        tempo = min(MAX_TEMPO, max(1.0, ln.duration_s / room))
        out.append((ln, tempo))
    return out


def caption_words(fitted: list[tuple[Line, float]]) -> list[CaptionWord]:
    """Word timings spread over each line by length (TTS speaks at an even pace)."""
    words: list[CaptionWord] = []
    for ln, tempo in fitted:
        toks = [t for t in re.split(r"\s+", ln.text.strip()) if t]
        if not toks:
            continue
        span = ln.duration_s / tempo * 0.96
        weights = [len(t) + 2 for t in toks]
        total = sum(weights)
        t = ln.t_in + 0.05
        for tok, w in zip(toks, weights, strict=True):
            d = span * w / total
            a = max(t, words[-1].t_out if words else 0.0)
            words.append(CaptionWord(t_in=round(a, 3), t_out=round(a + max(0.05, d), 3), text=tok[:64]))
            t += d
    return words


def mix(video: Path, out: Path, *, workdir: Path, total_s: float, lines: list[Line] | None = None,
        music: Path | None = None, music_db: float = -16.0, captions: bool = False, caption_style: str = "clean",
        width: int = 0, height: int = 0, accent: str = "#FFD400") -> None:  # fmt: skip
    """Replace ``video``'s (silent) audio with voiceover + music, optionally burn voiceover captions."""
    workdir.mkdir(parents=True, exist_ok=True)
    fitted = fit_lines(lines or [], total_s)
    inputs: list[str] = []
    parts: list[str] = []
    for i, (ln, tempo) in enumerate(fitted):
        inputs += ["-i", str(ln.path)]
        tempo_f = f"atempo={tempo:.4f}," if tempo > 1.001 else ""
        delay = round(ln.t_in * 1000)
        parts.append(
            f"[{i}:a]{tempo_f}aresample=48000,aformat=channel_layouts=stereo,adelay={delay}|{delay}[v{i}]"
        )
    n_voice = len(fitted)
    graph = parts[:]
    if n_voice:
        mixins = "".join(f"[v{i}]" for i in range(n_voice))
        graph.append(f"{mixins}amix=inputs={n_voice}:duration=longest:dropout_transition=0:normalize=0,"
                     f"apad=whole_dur={total_s:.3f},atrim=0:{total_s:.3f}[voice]")  # fmt: skip
    if music is not None:
        mi = n_voice
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        graph.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total_s:.3f},"
                     f"volume={music_db}dB,afade=t=in:d=0.6,afade=t=out:st={max(0.0, total_s - 1.5):.3f}:d=1.5[m]")  # fmt: skip
        if n_voice:
            graph.append("[voice]asplit=2[vo][sc];[m][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:"
                         "release=350[md];[vo][md]amix=inputs=2:duration=first:normalize=0[mixed]")  # fmt: skip
        else:
            graph.append("[m]anull[mixed]")
    elif n_voice:
        graph.append("[voice]anull[mixed]")
    else:
        inputs += ["-f", "lavfi", "-t", f"{total_s:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph.append("[0:a]anull[mixed]")
    graph.append("[mixed]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    audio = workdir / "motion_audio.m4a"
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(graph),
          "-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", f"{total_s:.3f}", str(audio)],
         "motion audio")  # fmt: skip
    vf: list[str] = []
    if captions and fitted and width and height:
        ass = write_ass(caption_words(fitted), workdir / "vo.ass", width=width, height=height, style=caption_style,
                        accent=accent, top=True)  # fmt: skip
        vf = ["-vf", subtitles_filter(ass)]
    vcodec = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-threads", "1", "-x264-params",
               "rc-lookahead=4:ref=2", "-pix_fmt", "yuv420p"] if vf else ["-c:v", "copy"])  # fmt: skip
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2", "-i", str(video),
          "-i", str(audio), *vf, "-map", "0:v", "-map", "1:a", *vcodec, "-c:a", "copy", "-shortest",
          "-movflags", "+faststart", str(out)], "motion finishing")  # fmt: skip
