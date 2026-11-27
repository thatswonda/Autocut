"""EDL -> MP4 with FFmpeg.

Pipeline (one generation of compression in the delivered file):
1. Every segment is cut from the original file, reframed to the target aspect around the subject,
   auto-corrected (exposure and white balance measured on the shot), moved (zoom, pan, shake,
   punch-in), given its transition halves, graded (3D LUT look + fine adjustments + vignette, sharpening,
   grain) and written as a near-lossless intermediate with PCM audio, trimmed to an exact frame count on
   the output timeline (so audio and video can never drift and there are no gaps at cuts).
2. Audio pass over the joined intermediates: noise reduction, sound effects, music bed with ducking,
   loudness to -14 LUFS.
3. Video pass: the joined intermediates are encoded once into one continuous H.264 stream (no joins in
   the delivered file), with word captions burned in, and muxed with the audio.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from autoedit_pipeline.media import FF_DECODE, FF_X264, probe
from autoedit_schemas.edl import EDL, CaptionWord, Color, EffectType
from autoedit_schemas.signals import Signals

from .captions import subtitles_filter, write_ass
from .effects import EDGES, PieceFx, motion_chain
from .looks import LOOKS, look_filters
from .sfx import sfx_file

FFMPEG = os.environ.get("AUTOEDIT_FFMPEG", "ffmpeg")
FPS = 30
ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "16:9": (16, 9), "4:5": (4, 5)}
# Intermediates: visually lossless, fast to write, PCM audio (no AAC priming gaps at joins).
INTER_V = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv420p", "-g", "30"]
INTER_A = ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
FINAL_PRESET = os.environ.get("AUTOEDIT_X264_PRESET", "faster")
# Grading filters and the encoder are slice-threaded: 2 threads nearly halve render time (~390 MB peak
# at 1080p); 1 thread fits a 512 MB server (~300 MB). AUTOEDIT_RENDER_THREADS picks.
_T = str(max(1, min(4, int(os.environ.get("AUTOEDIT_RENDER_THREADS", "2")))))
PIECE_THREADS = ["-filter_threads", _T, "-filter_complex_threads", _T, "-threads", _T]
FINAL_CRF = os.environ.get("AUTOEDIT_X264_CRF", "18")


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


def _run(cmd: list[str], what: str, timeout: float = 1800) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RenderError(f"{what} failed: {proc.stderr.decode('utf-8', 'replace')[-600:]}")
    return proc


# ------------------------------------------------------------------------------------ framing


def _subject_center(sig: Signals | None, a: float, b: float) -> tuple[float, float] | None:
    """Median subject position over the segment, or None when nobody is on screen."""
    if sig is None:
        return None
    pts = [r.subject_center for r in sig.per_second if a - 0.5 <= r.t < b and r.subject_center]
    if not pts:
        return None
    return statistics.median(p.x for p in pts), statistics.median(p.y for p in pts)


def _frame_filter(src_w: int, src_h: int, out_w: int, out_h: int, center: tuple[float, float] | None,
                  zoom: float = 1.0) -> str:  # fmt: skip
    """Crop to the target aspect following the subject. Without a subject, blur-fill instead of a blind
    crop when cropping would throw away most of the picture (screen recordings, wide scenery).
    ``zoom`` > 1 crops tighter (punch-in) from the full-resolution source."""
    src_ar, out_ar = src_w / src_h, out_w / out_h
    if abs(src_ar - out_ar) < 0.01 and zoom <= 1.001:
        return f"scale={out_w}:{out_h}:flags=lanczos"
    if src_ar > out_ar:
        cw, ch = float(src_h * out_ar), float(src_h)
    else:
        cw, ch = float(src_w), float(src_w / out_ar)
    kept = (cw * ch) / (src_w * src_h)
    if kept < 0.42 and center is None:
        fw, fh = round(out_w * zoom), round(out_h * zoom)
        return (
            f"split[a][b];[a]scale={out_w // 4}:{out_h // 4}:force_original_aspect_ratio=increase,"
            f"crop={out_w // 4}:{out_h // 4},gblur=sigma=6,eq=brightness=-0.10:saturation=0.8,"
            f"scale={out_w}:{out_h}[bg];[b]scale={fw}:{fh}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
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


# ------------------------------------------------------------------------------------- color


def auto_correct(src: Path, a: float, b: float, *, strength: float = 0.7) -> str:
    """Per-shot exposure and white balance, measured on three frames (gray-world on the mid-tones)."""
    times = [a + (b - a) * f for f in (0.2, 0.5, 0.8)]
    px: list[np.ndarray] = []
    for t in times:
        proc = subprocess.run(
            [FFMPEG, "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
             "-vf", "scale=64:64,format=rgb24", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60, check=False,
        )  # fmt: skip
        if proc.returncode == 0 and len(proc.stdout) == 64 * 64 * 3:
            px.append(np.frombuffer(proc.stdout, dtype=np.uint8).reshape(-1, 3) / 255.0)
    if not px:
        return ""
    rgb = np.concatenate(px)
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722])
    mid = rgb[(luma > 0.12) & (luma < 0.88)]
    if len(mid) < 200:
        mid = rgb
    means = mid.mean(axis=0)
    grey = float(means.mean())
    gains = np.clip(1 + (grey / np.maximum(means, 1e-3) - 1) * strength * 0.6, 0.92, 1.08)
    parts = []
    if np.max(np.abs(gains - 1)) > 0.008:
        parts.append(f"colorchannelmixer=rr={gains[0]:.3f}:gg={gains[1]:.3f}:bb={gains[2]:.3f}")
    level = float(np.median(luma))
    if level > 0.03:
        # gamma > 1 brightens in eq; aim mid-tones at ~0.45, gently.
        gamma = float(
            np.clip((np.log(0.45) / np.log(max(min(level, 0.95), 0.05))) ** (strength * 0.5), 0.8, 1.35)
        )
        if abs(gamma - 1) > 0.02:
            parts.append(f"eq=gamma={gamma:.3f}")
    return ",".join(parts)


def adjust_filter(color: Color) -> str:
    """The EDL's fine adjustments (-100..100 each), applied before the look."""
    brightness = color.exposure / 100 * 0.14
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


# ------------------------------------------------------------------------------------ pieces


def encode_piece(src: Path, out: Path, *, t_in: float | None, t_out: float | None, frames: int, out_w: int,
                 out_h: int, center: tuple[float, float] | None = None, look: str = "none",
                 adjust: Color | None = None, fx: PieceFx | None = None, zoom: float = 1.0, speed: float = 1.0,
                 correct: bool = True) -> float:  # fmt: skip
    """Write one intermediate piece with exactly ``frames`` frames; returns its duration."""
    info = probe(src)
    start = t_in or 0.0
    end = t_out if t_out is not None else info.duration_s
    speed = speed if 1.01 < speed <= 4.0 else 1.0
    dur = frames / FPS
    src_dur = max(0.1, min(end - start, dur * speed + 0.5))
    fx = fx or PieceFx()
    chain = "[0:v]" + (f"setpts=(PTS-STARTPTS)/{speed:.4f}," if speed != 1.0 else "setpts=PTS-STARTPTS,")
    chain += f"fps={FPS},{_frame_filter(info.display_width, info.display_height, out_w, out_h, center, zoom)}"
    move = motion_chain(fx, dur, out_w, out_h)
    if move:
        chain += f",{move}"
    if correct:
        ac = auto_correct(src, start, min(end, start + src_dur))
        if ac:
            chain += f",{ac}"
    if adjust is not None:
        adj = adjust_filter(adjust)
        if adj:
            chain += f",{adj}"
    lf = look_filters(look, height=out_h) if look in LOOKS else ""
    if lf:
        chain += f",{lf}"
    chain += ",setsar=1,format=yuv420p[v]"
    fade = min(0.012, dur / 6)
    if info.has_audio:
        tempo = f"{_atempo(speed)}," if speed != 1.0 else ""
        achain = (f"[0:a]asetpts=PTS-STARTPTS,{tempo}aresample=48000,aformat=channel_layouts=stereo,apad,"
                  f"atrim=0:{dur:.6f},afade=t=in:d={fade:.3f},afade=t=out:st={dur - fade:.4f}:d={fade:.3f}[a]")  # fmt: skip
        inputs = ["-ss", f"{start:.3f}", "-t", f"{src_dur:.3f}", "-i", str(src)]
        amap = ["-map", "[a]"]
        graph = chain + ";" + achain
    else:
        inputs = ["-ss", f"{start:.3f}", "-t", f"{src_dur:.3f}", "-i", str(src),
                  "-f", "lavfi", "-t", f"{dur:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]  # fmt: skip
        amap = ["-map", "1:a"]
        graph = chain
    # Freeze-proof: pad the video with its last frame if the source runs short, then cut to exact frames.
    graph = graph.replace(
        ",setsar=1,format=yuv420p[v]", ",tpad=stop_mode=clone:stop=-1,setsar=1,format=yuv420p[v]"
    )
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *PIECE_THREADS, *inputs,
          "-filter_complex", graph, "-map", "[v]", *amap, "-frames:v", str(frames), *INTER_V, *INTER_A,
          "-t", f"{dur:.6f}", str(out)], "segment render")  # fmt: skip
    return dur


def _piece_fx(edl: EDL, idx: int, a: float, b: float) -> tuple[PieceFx, float]:
    """Effects for segment ``idx`` spanning [a, b) on the output timeline; returns (fx, punch zoom)."""
    fx = PieceFx()
    zoom = 1.0
    for e in edl.effects:
        if e.t_out <= a + 0.02 or e.t_in >= b - 0.02:
            continue
        kind = e.type
        strength = (
            float(e.params.get("strength", 1.0))
            if isinstance(e.params.get("strength", 1.0), int | float)
            else 1.0
        )
        if kind is EffectType.TRANSITION:
            style = str(e.params.get("style", "zoom"))
            if style not in EDGES:
                continue
            cut = (e.t_in + e.t_out) / 2
            if abs(cut - a) < 0.08:
                fx.edge_in = style
            elif abs(cut - b) < 0.08:
                fx.edge_out = style
        elif kind is EffectType.PUNCH_ZOOM:
            zoom = max(
                zoom,
                float(e.params.get("scale", 1.15))
                if isinstance(e.params.get("scale"), int | float)
                else 1.15,
            )
        elif kind in (EffectType.ZOOM_IN, EffectType.ZOOM_OUT, EffectType.PAN_LEFT, EffectType.PAN_RIGHT):
            fx.move, fx.strength = kind.value, strength
        elif kind is EffectType.SHAKE:
            if e.t_out - e.t_in > 0.8:
                fx.move, fx.strength = "shake", strength
            else:
                fx.hits.append((max(0.0, e.t_in - a), "shake"))
        elif kind in (EffectType.GLITCH, EffectType.FLASH):
            fx.hits.append((max(0.0, e.t_in - a), kind.value))
    return fx, min(1.4, zoom)


# ------------------------------------------------------------------------------------- render


def render_edl(
    edl: EDL,
    clip_files: dict[str, Path],
    signals: dict[str, Signals],
    out: Path,
    *,
    workdir: Path,
    short_edge: int = 1080,
    intro: Path | None = None,
    outro: Path | None = None,
    music: Path | None = None,
    accent: str = "#FFD400",
    progress: Callable[[float], None] | None = None,
) -> EditRenderResult:
    t0 = time.perf_counter()
    workdir.mkdir(parents=True, exist_ok=True)
    out_w, out_h = output_size(edl.meta.aspect, short_edge)
    look = edl.color.preset if edl.color.preset in LOOKS else "natural"
    parts: list[Path] = []
    steps = len(edl.segments) + (1 if intro else 0) + (1 if outro else 0) + 3
    done = 0

    def tick() -> None:
        nonlocal done
        done += 1
        if progress:
            progress(min(0.99, done / steps))

    offset = 0.0
    if intro:
        p = workdir / "intro.mkv"
        n = max(1, round(probe(intro).duration_s * FPS))
        offset += encode_piece(
            intro, p, t_in=None, t_out=None, frames=n, out_w=out_w, out_h=out_h, correct=False
        )
        parts.append(p)
        tick()
    starts = edl.segment_starts()
    for i, seg in enumerate(edl.segments):
        a = starts[seg.id]
        b = a + seg.output_duration_s
        frames = max(1, round(b * FPS) - round(a * FPS))
        fx, zoom = _piece_fx(edl, i, a, b)
        sig = signals.get(seg.clip_id)
        p = workdir / f"seg{i:03d}.mkv"
        encode_piece(
            clip_files[seg.clip_id], p, t_in=seg.src_in, t_out=seg.src_out, frames=frames, out_w=out_w,
            out_h=out_h, center=_subject_center(sig, seg.src_in, seg.src_out), look=look, adjust=edl.color,
            fx=fx, zoom=zoom, speed=seg.speed,
        )  # fmt: skip
        parts.append(p)
        tick()
    main_s = round(sum(s.output_duration_s for s in edl.segments) * FPS) / FPS
    if outro:
        p = workdir / "outro.mkv"
        n = max(1, round(probe(outro).duration_s * FPS))
        encode_piece(outro, p, t_in=None, t_out=None, frames=n, out_w=out_w, out_h=out_h, correct=False)
        parts.append(p)
        tick()
    if not parts:
        raise RenderError("nothing to render")
    total = sum(_frames_of(p) for p in parts) / FPS
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".partial.mp4")
    finish(parts, tmp, edl, workdir=workdir, width=out_w, height=out_h, offset_s=offset, music=music,
           accent=accent, total_s=total, main_s=main_s, tick=tick)  # fmt: skip
    tmp.replace(out)
    tick()
    return EditRenderResult(path=out, width=out_w, height=out_h, duration_s=round(total, 3),
                            seconds=time.perf_counter() - t0, segments=len(edl.segments))  # fmt: skip


def _frames_of(p: Path) -> int:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                           "stream=nb_read_packets", "-of", "csv=p=0", str(p)], capture_output=True, text=True,
                          check=False)  # fmt: skip
    try:
        return int(proc.stdout.strip().split(",")[0])
    except ValueError:
        return 0


def _concat_list(parts: list[Path], workdir: Path) -> Path:
    listing = workdir / "pieces.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    return listing


def finish(parts: list[Path], out: Path, edl: EDL, *, workdir: Path, width: int, height: int,
           offset_s: float = 0.0, music: Path | None = None, accent: str = "#FFD400", total_s: float = 0.0,
           main_s: float | None = None, tick: Callable[[], None] | None = None) -> None:  # fmt: skip
    """Audio pass (denoise, sound effects, music with ducking, loudness) then one video encode."""
    listing = _concat_list(parts, workdir)
    lufs = edl.audio.normalize_lufs
    inputs = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    graph = ["[0:a]" + ("afftdn=nr=10:nf=-40:tn=1," if edl.audio.denoise else "") + "highpass=f=70,"
             "acompressor=threshold=-20dB:ratio=3:attack=10:release=200:makeup=2[voice]"]  # fmt: skip
    mix = ["[voice]"]
    idx = 1
    if music is not None and edl.audio.music.enabled:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        end = main_s if main_s else total_s
        graph.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total_s:.3f},"
                     f"volume={edl.audio.music.volume_db}dB,afade=t=in:d=0.8,"
                     f"afade=t=out:st={max(0.0, offset_s + end - 1.5):.3f}:d=1.5[m]")  # fmt: skip
        if edl.audio.music.duck_under_speech:
            graph[0] = graph[0].replace("[voice]", ",asplit=2[voice][vkey]")
            graph.append("[m][vkey]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[md]")
            mix.append("[md]")
        else:
            mix.append("[m]")
        idx += 1
    for k, s in enumerate(edl.audio.sfx[:60]):
        inputs += ["-i", str(sfx_file(s.type.value))]
        delay = round((s.t + offset_s) * 1000)
        graph.append(f"[{idx}:a]volume={s.volume_db}dB,adelay={delay}|{delay}[x{k}]")
        mix.append(f"[x{k}]")
        idx += 1
    if len(mix) > 1:
        graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0,"
                     f"loudnorm=I={lufs}:TP=-1.5:LRA=11[a]")  # fmt: skip
    else:
        graph.append(f"[voice]loudnorm=I={lufs}:TP=-1.5:LRA=11[a]")
    audio = workdir / "final_audio.m4a"
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(graph),
          "-map", "[a]", "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total_s:.4f}", str(audio)],
         "audio finishing")  # fmt: skip
    if tick:
        tick()
    vf = [f"setpts=N/({FPS}*TB)"]
    words = edl.captions.words if edl.captions.enabled else []
    if words:
        shifted = [CaptionWord(t_in=w.t_in + offset_s, t_out=w.t_out + offset_s, text=w.text, emphasis=w.emphasis)
                   for w in words]  # fmt: skip
        ass = write_ass(shifted, workdir / "captions.ass", width=width, height=height, style=edl.captions.style,
                        accent=accent)  # fmt: skip
        vf.append(subtitles_filter(ass))
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2", "-f", "concat", "-safe",
          "0", "-i", str(listing), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-vf", ",".join(vf),
          "-r", str(FPS), "-c:v", "libx264", "-preset", FINAL_PRESET, "-crf", FINAL_CRF, "-profile:v", "high",
          "-pix_fmt", "yuv420p", "-threads", _T, "-x264-params", "rc-lookahead=10:ref=3", "-c:a", "copy",
          "-shortest", "-movflags", "+faststart", str(out)], "video encoding")  # fmt: skip


def cut_clip(src: Path, out: Path, t_in: float, t_out: float) -> float:
    """Cut part of a finished video (to save as a reusable template). Re-encoded for frame accuracy."""
    info = probe(src)
    dur = max(0.2, min(t_out, info.duration_s) - max(0.0, t_in))
    out.parent.mkdir(parents=True, exist_ok=True)
    amap = ["-map", "0:a:0"] if info.has_audio else []
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *FF_DECODE,
          "-ss", f"{max(0.0, t_in):.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-map", "0:v:0", *amap,
          "-c:v", "libx264", "-preset", "fast", "-crf", "17", *FF_X264, "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-b:a", "192k", "-movflags", "+faststart", str(out)], "template cut")  # fmt: skip
    return dur


def poster(src: Path, out: Path, t: float = 0.5) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *FF_DECODE, "-ss", f"{t:.2f}",
          "-i", str(src), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", str(out)], "poster")  # fmt: skip
    return out


def join_pieces(pieces: list[Path], out: Path, *, aspect: str, short_edge: int, workdir: Path) -> float:
    """Normalise finished pieces (intro, main video, outro) to one size and join them, loudness-levelled."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_w, out_h = output_size(aspect, short_edge)
    normed: list[Path] = []
    for i, src in enumerate(pieces):
        p = workdir / f"piece{i:02d}.mkv"
        n = max(1, round(probe(src).duration_s * FPS))
        encode_piece(src, p, t_in=None, t_out=None, frames=n, out_w=out_w, out_h=out_h, correct=False)
        normed.append(p)
    total = sum(_frames_of(p) for p in normed) / FPS
    empty = EDL.model_validate({"clips": [{"id": "x", "order": 1, "duration_s": max(total, 0.1)}],
                                "segments": [{"id": "s1", "clip_id": "x", "src_in": 0, "src_out": max(total, 0.1)}],
                                "captions": {"enabled": False}, "audio": {"denoise": False}})  # fmt: skip
    finish(normed, out, empty, workdir=workdir, width=out_w, height=out_h, total_s=total)
    return total
