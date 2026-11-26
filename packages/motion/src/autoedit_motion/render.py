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
from .page import build_page, output_size

log = logging.getLogger(__name__)

FFMPEG = os.environ.get("AUTOEDIT_FFMPEG", "ffmpeg")


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
    width, height = output_size(comp.meta.aspect, short_edge)
    workdir.mkdir(parents=True, exist_ok=True)
    asset_dir = workdir / "assets"
    asset_dir.mkdir(exist_ok=True)
    urls: dict[str, str] = {}
    for aid, src in assets.items():
        dst = asset_dir / f"{aid}{src.suffix.lower()}"
        if not dst.exists():
            shutil.copyfile(src, dst)
        urls[aid] = f"assets/{dst.name}"
    page_path = workdir / "index.html"
    page_path.write_text(build_page(comp, urls), encoding="utf-8")

    frames = max(1, round(comp.meta.duration_s * fps))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out.with_suffix(".partial.mp4")
    cmd = [
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "1",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-threads", "1", "-x264-params", "rc-lookahead=4:ref=2",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp_out),
    ]  # fmt: skip
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert enc.stdin is not None
    warnings: list[str] = []
    try:
        with Browser(width, height) as page:
            page.goto(page_path.resolve().as_uri())
            warnings = page.evaluate("window.__ready()") or []
            for i in range(frames):
                page.evaluate(f"window.__seek({i / fps:.5f})")
                enc.stdin.write(page.screenshot("jpeg", 92))
                if progress and (i % 15 == 0 or i == frames - 1):
                    progress((i + 1) / frames)
        enc.stdin.close()
        code = enc.wait(timeout=600)
        if code != 0:
            err = enc.stderr.read().decode("utf-8", "replace")[-400:] if enc.stderr else ""
            raise RenderError(f"encoding failed: {err}")
    except Exception:
        enc.kill()
        tmp_out.unlink(missing_ok=True)
        raise
    tmp_out.replace(out)
    return RenderResult(
        path=out, width=width, height=height, fps=fps, frames=frames, duration_s=comp.meta.duration_s,
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
