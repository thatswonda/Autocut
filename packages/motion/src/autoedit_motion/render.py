"""Frame-stepped renderer: headless Chromium plays the composition page, FFmpeg encodes the frames.

Every frame is produced by seeking the page to an exact time, so the output is deterministic and
independent of how fast the machine is.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autoedit_schemas.composition import Composition

from .cdp import Browser
from .page import FootageFrames, build_page, output_size

log = logging.getLogger(__name__)

FFMPEG = os.environ.get("AUTOEDIT_FFMPEG", "ffmpeg")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".3gp"}


def extract_footage(comp: Composition, videos: dict[str, Path], workdir: Path, *, width: int, height: int,
                    fps: int) -> dict[str, FootageFrames]:  # fmt: skip
    """JPEG frames for every footage scene, timed to the scene and cropped to fill the frame."""
    out: dict[str, FootageFrames] = {}
    for sc in comp.scenes:
        if sc.template != "footage" or not sc.clip_id or sc.clip_id not in videos:
            continue
        assert sc.src_in is not None and sc.src_out is not None
        scene_s = sc.t_out - sc.t_in + 0.1
        src_s = max(0.1, sc.src_out - sc.src_in)
        d = workdir / "footage" / sc.id
        d.mkdir(parents=True, exist_ok=True)
        vf = (f"setpts=(PTS-STARTPTS)*{scene_s / src_s:.5f},fps={fps},"
              f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}")  # fmt: skip
        proc = subprocess.run(
            [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-filter_threads", "1", "-threads", "2",
             "-ss", f"{sc.src_in:.3f}", "-t", f"{src_s:.3f}", "-i", str(videos[sc.clip_id]), "-an", "-vf", vf,
             "-q:v", "3", "-start_number", "0", str(d / "%05d.jpg")],
            capture_output=True, timeout=600, check=False,
        )  # fmt: skip
        count = len(list(d.glob("*.jpg")))
        if proc.returncode != 0 or count == 0:
            raise RenderError(
                f"could not read footage for {sc.id}: {proc.stderr.decode('utf-8', 'replace')[-200:]}"
            )
        out[sc.id] = FootageFrames(prefix=f"footage/{sc.id}/", count=count, fps=fps)
    return out


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    path: Path
    width: int
    height: int
    fps: int
    frames: int
    duration_s: float
    seconds: float
    warnings: list[str] = field(default_factory=list)


def render_composition(
    comp: Composition,
    assets: dict[str, Path],
    out: Path,
    *,
    workdir: Path,
    short_edge: int = 720,
    fps: int | None = None,
    progress: Callable[[float], None] | None = None,
) -> RenderResult:
    """Render ``comp`` to an H.264 MP4 at ``out``. ``assets`` maps asset ids to local files."""
    t0 = time.perf_counter()
    fps = fps or comp.meta.fps
    out_w, out_h = output_size(comp.meta.aspect, short_edge)
    # Small servers animate at a capped size and upscale the frames (AUTOEDIT_MOTION_MAX_EDGE, e.g. 720).
    cap = int(os.environ.get("AUTOEDIT_MOTION_MAX_EDGE", "0") or 0)
    width, height = output_size(comp.meta.aspect, min(short_edge, cap) if cap >= 360 else short_edge)
    workdir.mkdir(parents=True, exist_ok=True)
    asset_dir = workdir / "assets"
    asset_dir.mkdir(exist_ok=True)
    urls: dict[str, str] = {}
    videos = {aid: src for aid, src in assets.items() if src.suffix.lower() in VIDEO_EXT}
    for aid, src in assets.items():
        if aid in videos:
            continue
        dst = asset_dir / f"{aid}{src.suffix.lower()}"
        if not dst.exists():
            shutil.copyfile(src, dst)
        urls[aid] = f"assets/{dst.name}"
    footage = extract_footage(comp, videos, workdir, width=width, height=height, fps=fps)
    page_path = workdir / "index.html"
    page_path.write_text(build_page(comp, urls, footage), encoding="utf-8")

    frames = max(1, round(comp.meta.duration_s * fps))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out.with_suffix(".partial.mp4")
    # 1. Capture every frame to disk with only Chrome running; 2. close Chrome; 3. encode. Running the
    # encoder alongside Chrome doubled the peak memory (it killed 512 MB servers).
    frame_dir = workdir / "frames"
    frame_dir.mkdir(exist_ok=True)
    warnings: list[str] = []
    try:
        with Browser(width, height) as page:
            page.goto(page_path.resolve().as_uri())
            warnings = page.evaluate("window.__ready()") or []
            for i in range(frames):
                page.evaluate(f"window.__seek({i / fps:.5f})")
                (frame_dir / f"{i:05d}.jpg").write_bytes(page.screenshot("jpeg", 92))
                if progress and (i % 15 == 0 or i == frames - 1):
                    progress((i + 1) / frames * 0.85)
        scale = [] if (out_w, out_h) == (width, height) else [
            "-vf", f"scale={out_w}:{out_h}:flags=lanczos,unsharp=3:3:0.35"]  # fmt: skip
        proc = subprocess.run(
            [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "1",
             "-framerate", str(fps), "-i", str(frame_dir / "%05d.jpg"),
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a", "-shortest", *scale,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-threads", "1",
             "-x264-params", "rc-lookahead=4:ref=2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
             str(tmp_out)],
            capture_output=True, timeout=1800, check=False,
        )  # fmt: skip
        if proc.returncode != 0:
            raise RenderError(f"encoding failed: {proc.stderr.decode('utf-8', 'replace')[-400:]}")
        if progress:
            progress(1.0)
    except Exception:
        tmp_out.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
    tmp_out.replace(out)
    return RenderResult(
        path=out, width=out_w, height=out_h, fps=fps, frames=frames, duration_s=comp.meta.duration_s,
        seconds=time.perf_counter() - t0, warnings=warnings,
    )  # fmt: skip


def snapshot(comp: Composition, assets: dict[str, Path], times: list[float], *, workdir: Path,
             short_edge: int = 540) -> list[bytes]:  # fmt: skip
    """PNG stills at the given times (storyboard thumbnails, self-review)."""
    width, height = output_size(comp.meta.aspect, short_edge)
    workdir.mkdir(parents=True, exist_ok=True)
    asset_dir = workdir / "assets"
    asset_dir.mkdir(exist_ok=True)
    urls = {}
    for aid, src in assets.items():
        dst = asset_dir / f"{aid}{src.suffix.lower()}"
        if not dst.exists():
            shutil.copyfile(src, dst)
        urls[aid] = f"assets/{dst.name}"
    page_path = workdir / "index.html"
    page_path.write_text(build_page(comp, urls), encoding="utf-8")
    shots = []
    with Browser(width, height) as page:
        page.goto(page_path.resolve().as_uri())
        page.evaluate("window.__ready()")
        for t in times:
            page.evaluate(f"window.__seek({t:.4f})")
            shots.append(page.screenshot("png"))
    return shots


def layout_warnings(
    comp: Composition, images: dict[str, Path], *, workdir: Path, short_edge: int = 540
) -> list[str]:
    """Lay the storyboard out in the browser and report scenes whose text could not fit at a readable
    size (self-review, PRD 5.10 step 8). Footage scenes use their poster frame."""
    width, height = output_size(comp.meta.aspect, short_edge)
    workdir.mkdir(parents=True, exist_ok=True)
    urls = {aid: p.resolve().as_uri() for aid, p in images.items()}
    page_path = workdir / "review.html"
    page_path.write_text(build_page(comp, urls), encoding="utf-8")
    with Browser(width, height) as page:
        page.goto(page_path.resolve().as_uri())
        found = page.evaluate("window.__ready()") or []
    return [str(w) for w in found]
