"""EDL -> MP4 with FFmpeg.

Each segment is cut from the original file, reframed to the target aspect around the subject (with
punch-in zooms from the EDL's effects), sped up when asked, exposure corrected, color graded with the
EDL's adjustments, and encoded with identical parameters; intro/outro clips are normalised the same way.
The joined video then gets burned-in word captions, gentle noise reduction, an optional music bed that
ducks under speech, and loudness levelling to -14 LUFS.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autoedit_pipeline.media import FF_DECODE, FF_X264, probe
from autoedit_schemas.edl import EDL, CaptionWord, Color, EffectType
from autoedit_schemas.signals import Signals

from .captions import subtitles_filter, write_ass
from .presets import get_preset

FFMPEG = os.environ.get("AUTOEDIT_FFMPEG", "ffmpeg")
FPS = 30
ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "16:9": (16, 9), "4:5": (4, 5)}


class RenderError(RuntimeError):
    pass


@dataclass
class EditRenderResult:
    path: Path
    width: int
    height: int
    duration_s: float
    seconds: float
    segments: int
    notes: list[str] = field(default_factory=list)


def output_size(aspect: str, short_edge: int) -> tuple[int, int]:
    a, b = ASPECTS.get(aspect, (9, 16))
    if a <= b:
        w, h = short_edge, round(short_edge * b / a)
    else:
        w, h = round(short_edge * a / b), short_edge
    return w - w % 2, h - h % 2


def _run(cmd: list[str], what: str, timeout: float = 1800) -> None:
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RenderError(f"{what} failed: {proc.stderr.decode('utf-8', 'replace')[-500:]}")


def _subject_center(sig: Signals | None, a: float, b: float) -> tuple[float, float] | None:
    """Median subject position over the segment, or None when nobody is on screen."""
    if sig is None:
        return None
    pts = [r.subject_center for r in sig.per_second if a - 0.5 <= r.t < b and r.subject_center]
    if not pts:
        return None
    return statistics.median(p.x for p in pts), statistics.median(p.y for p in pts)


def _exposure_fix(sig: Signals | None, a: float, b: float) -> float:
    if sig is None:
        return 0.0
    vals = [r.brightness for r in sig.per_second if a <= r.t < b and not r.black]
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    return max(-0.06, min(0.10, (0.46 - mean) * 0.45))


def _frame_filter(src_w: int, src_h: int, out_w: int, out_h: int, center: tuple[float, float] | None,
                  zoom: float = 1.0) -> str:  # fmt: skip
    """Crop to the target aspect following the subject. Without a subject, blur-fill instead of a blind
    crop when cropping would throw away most of the picture (screen recordings, wide scenery).
    ``zoom`` > 1 crops tighter around the subject (punch-in)."""
    src_ar, out_ar = src_w / src_h, out_w / out_h
    if abs(src_ar - out_ar) < 0.01 and zoom <= 1.001:
        return f"scale={out_w}:{out_h}:flags=lanczos"
    if src_ar > out_ar:
        cw, ch = float(src_h * out_ar), float(src_h)
    else:
        cw, ch = float(src_w), float(src_w / out_ar)
    kept = (cw * ch) / (src_w * src_h)
    if kept < 0.42 and center is None:
        # Blur-fill. A punch-in here enlarges the framed picture rather than cropping a narrow sliver.
        fw, fh = round(out_w * zoom), round(out_h * zoom)
        return (
            f"split[a][b];[a]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
            f"boxblur=20:2,eq=brightness=-0.08[bg];[b]scale={fw}:{fh}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    cw, ch = cw / max(zoom, 1.0), ch / max(zoom, 1.0)
    cx, cy = center or (0.5, 0.45)
    x = min(max(0, cx * src_w - cw / 2), src_w - cw)
    y = min(max(0, cy * src_h - ch * 0.42), src_h - ch)
    return (
        f"crop={int(cw) - int(cw) % 2}:{int(ch) - int(ch) % 2}:{int(x)}:{int(y)},"
        f"scale={out_w}:{out_h}:flags=lanczos"
    )


def adjust_filter(color: Color, exposure_fix: float = 0.0) -> str:
    """The EDL's fine adjustments (-100..100 each) as FFmpeg filters, applied before the preset look."""
    brightness = exposure_fix + color.exposure / 100 * 0.14
    contrast = 1 + color.contrast / 100 * 0.45
    saturation = 1 + color.saturation / 100 * 0.7
    parts = []
    if abs(brightness) > 0.004 or abs(contrast - 1) > 0.004 or abs(saturation - 1) > 0.004:
        parts.append(f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}")
    if abs(color.temperature) >= 2:
        kelvin = max(2500, min(12000, 6500 - color.temperature * 35))
        parts.append(f"colortemperature=temperature={kelvin:.0f}:mix=0.6")
    return ",".join(parts)


def _atempo(speed: float) -> str:
    steps = []
    while speed > 2.0:
        steps.append("atempo=2.0")
        speed /= 2.0
    steps.append(f"atempo={speed:.4f}")
    return ",".join(steps)


def _encode_piece(src: Path, out: Path, *, t_in: float | None, t_out: float | None, out_w: int, out_h: int,
                  center: tuple[float, float] | None, exposure: float, grade: str,
                  adjust: Color | None = None, zoom: float = 1.0, speed: float = 1.0) -> float:  # fmt: skip
    """Encode one piece; returns its output duration."""
    info = probe(src)
    start = t_in or 0.0
    end = t_out if t_out is not None else info.duration_s
    src_dur = max(0.1, end - start)
    speed = speed if 1.01 < speed <= 4.0 else 1.0
    dur = src_dur / speed
    vf = _frame_filter(info.display_width, info.display_height, out_w, out_h, center, zoom)
    chain = "[0:v]" + (f"setpts=(PTS-STARTPTS)/{speed:.4f}," if speed != 1.0 else "") + f"fps={FPS},{vf}"
    adj = adjust_filter(adjust, exposure) if adjust is not None else (
        f"eq=brightness={exposure:.3f}" if abs(exposure) > 0.005 else "")  # fmt: skip
    if adj:
        chain += f",{adj}"
    if grade != "null":
        chain += f",{grade}"
    chain += ",setsar=1,format=yuv420p[v]"
    fade = min(0.05, dur / 4)
    if info.has_audio:
        tempo = f"{_atempo(speed)}," if speed != 1.0 else ""
        achain = (f"[0:a]{tempo}aresample=48000,aformat=channel_layouts=stereo,"
                  f"afade=t=in:d={fade:.3f},afade=t=out:st={dur - fade:.3f}:d={fade:.3f}[a]")  # fmt: skip
        inputs = ["-ss", f"{start:.3f}", "-t", f"{src_dur:.3f}", "-i", str(src)]
        amap = ["-map", "[a]"]
        graph = chain + ";" + achain
    else:
        inputs = ["-ss", f"{start:.3f}", "-t", f"{src_dur:.3f}", "-i", str(src),
                  "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]  # fmt: skip
        amap = ["-map", "1:a"]
        graph = chain
    cmd = [
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *FF_DECODE, *inputs,
        "-filter_complex", graph, "-map", "[v]", *amap,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *FF_X264, "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(out),
    ]  # fmt: skip
    _run(cmd, "segment render")
    return dur


def render_edl(
    edl: EDL,
    clip_files: dict[str, Path],
    signals: dict[str, Signals],
    out: Path,
    *,
    workdir: Path,
    short_edge: int = 720,
    intro: Path | None = None,
    outro: Path | None = None,
    music: Path | None = None,
    accent: str = "#FFD400",
    progress: Callable[[float], None] | None = None,
) -> EditRenderResult:
    t0 = time.perf_counter()
    workdir.mkdir(parents=True, exist_ok=True)
    out_w, out_h = output_size(edl.meta.aspect, short_edge)
    grade = get_preset(edl.color.preset).ffmpeg
    parts: list[Path] = []
    total = 0.0
    steps = len(edl.segments) + (1 if intro else 0) + (1 if outro else 0) + 1
    done = 0

    def tick() -> None:
        nonlocal done
        done += 1
        if progress:
            progress(min(0.99, done / steps))

    if intro:
        p = workdir / "intro.mp4"
        total += _encode_piece(intro, p, t_in=None, t_out=None, out_w=out_w, out_h=out_h,
                               center=None, exposure=0.0, grade="null")  # fmt: skip
        parts.append(p)
        tick()
    intro_s = total
    starts = edl.segment_starts()
    for i, seg in enumerate(edl.segments):
        src = clip_files[seg.clip_id]
        sig = signals.get(seg.clip_id)
        p = workdir / f"seg{i:03d}.mp4"
        a = starts[seg.id]
        b = a + seg.output_duration_s
        zoom = max([float(e.params.get("scale", 1.15)) for e in edl.effects
                    if e.type is EffectType.PUNCH_ZOOM and e.t_in < b - 0.05 and e.t_out > a + 0.05] or [1.0])  # fmt: skip
        total += _encode_piece(
            src, p, t_in=seg.src_in, t_out=seg.src_out, out_w=out_w, out_h=out_h,
            center=_subject_center(sig, seg.src_in, seg.src_out),
            exposure=_exposure_fix(sig, seg.src_in, seg.src_out), grade=grade,
            adjust=edl.color, zoom=min(1.4, zoom), speed=seg.speed,
        )  # fmt: skip
        parts.append(p)
        tick()
    if outro:
        p = workdir / "outro.mp4"
        total += _encode_piece(outro, p, t_in=None, t_out=None, out_w=out_w, out_h=out_h,
                               center=None, exposure=0.0, grade="null")  # fmt: skip
        parts.append(p)
        tick()
    if not parts:
        raise RenderError("nothing to render")

    listing = workdir / "concat.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    joined = workdir / "joined.mp4"
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", str(listing), "-c", "copy", str(joined)], "join")  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".partial.mp4")
    finish(joined, tmp, edl, workdir=workdir, width=out_w, height=out_h, offset_s=intro_s, music=music,
           accent=accent, total_s=total)  # fmt: skip
    tmp.replace(out)
    tick()
    return EditRenderResult(path=out, width=out_w, height=out_h, duration_s=round(total, 3),
                            seconds=time.perf_counter() - t0, segments=len(edl.segments))  # fmt: skip


def finish(joined: Path, out: Path, edl: EDL, *, workdir: Path, width: int, height: int, offset_s: float = 0.0,
           music: Path | None = None, accent: str = "#FFD400", total_s: float = 0.0) -> None:  # fmt: skip
    """Captions, noise reduction, music bed with ducking, loudness. Video is re-encoded only for captions."""
    lufs = edl.audio.normalize_lufs
    vf: list[str] = []
    words = edl.captions.words if edl.captions.enabled else []
    if words:
        shifted = [CaptionWord(t_in=w.t_in + offset_s, t_out=w.t_out + offset_s, text=w.text, emphasis=w.emphasis)
                   for w in words]  # fmt: skip
        ass = write_ass(shifted, workdir / "captions.ass", width=width, height=height, style=edl.captions.style,
                        accent=accent)  # fmt: skip
        vf = ["-vf", subtitles_filter(ass)]
    voice = "[0:a]" + ("afftdn=nr=10:nf=-40:tn=1," if edl.audio.denoise else "") + "highpass=f=70"
    inputs = ["-i", str(joined)]
    if music is not None and edl.audio.music.enabled:
        vol = edl.audio.music.volume_db
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        duck = edl.audio.music.duck_under_speech
        graph = (f"{voice},asplit=2[v1][v2];"
                 f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB,"
                 f"afade=t=in:d=1.0,afade=t=out:st={max(0.0, total_s - 1.5):.2f}:d=1.5[m];"
                 + ("[m][v2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[md];" if duck
                    else "[m]anull[md];[v2]anullsink;")
                 + f"[v1][md]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                 f"loudnorm=I={lufs}:TP=-1.5:LRA=11[a]")  # fmt: skip
    else:
        graph = f"{voice},loudnorm=I={lufs}:TP=-1.5:LRA=11[a]"
    # Two passes: loudnorm looks ahead ~3 s, and doing it in the same run as the video makes FFmpeg
    # buffer that much raw video (~250 MB at 720p). Audio alone first, then video with the audio copied.
    audio = workdir / "final_audio.m4a"
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph,
          "-map", "[a]", "-vn", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(audio)], "audio finishing")  # fmt: skip
    vcodec = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *FF_X264, "-pix_fmt", "yuv420p"] if vf
              else ["-c:v", "copy"])  # fmt: skip
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *FF_DECODE, "-i", str(joined),
          "-i", str(audio), *vf, "-map", "0:v", "-map", "1:a", *vcodec, "-c:a", "copy", "-shortest",
          "-movflags", "+faststart", str(out)], "finishing")  # fmt: skip


def cut_clip(src: Path, out: Path, t_in: float, t_out: float) -> float:
    """Cut part of a finished video (to save as a reusable template). Re-encoded for frame accuracy."""
    info = probe(src)
    dur = max(0.2, min(t_out, info.duration_s) - max(0.0, t_in))
    out.parent.mkdir(parents=True, exist_ok=True)
    amap = ["-map", "0:a:0"] if info.has_audio else []
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *FF_DECODE,
          "-ss", f"{max(0.0, t_in):.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-map", "0:v:0", *amap,
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", *FF_X264, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
         "template cut")  # fmt: skip
    return dur


def poster(src: Path, out: Path, t: float = 0.5) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *FF_DECODE, "-ss", f"{t:.2f}",
          "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)], "poster")  # fmt: skip
    return out


def join_pieces(pieces: list[Path], out: Path, *, aspect: str, short_edge: int, workdir: Path) -> float:
    """Normalise finished pieces (intro, main video, outro) to one size and join them, loudness-levelled."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_w, out_h = output_size(aspect, short_edge)
    normed: list[Path] = []
    total = 0.0
    for i, src in enumerate(pieces):
        p = workdir / f"piece{i:02d}.mp4"
        total += _encode_piece(src, p, t_in=None, t_out=None, out_w=out_w, out_h=out_h,
                               center=None, exposure=0.0, grade="null")  # fmt: skip
        normed.append(p)
    listing = workdir / "pieces.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in normed))
    joined = workdir / "pieces_joined.mp4"
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", str(listing), "-c", "copy", str(joined)], "join")  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined), "-c:v", "copy",
          "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
          "-movflags", "+faststart", str(out)], "loudness")  # fmt: skip
    return total
