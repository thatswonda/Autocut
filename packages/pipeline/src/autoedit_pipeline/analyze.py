"""Stage A orchestration for one clip: probe -> proxy -> local signals, cached by content hash.

Every step is idempotent: artifacts are written atomically and reused when the source hash and the
pipeline version match, so a retried or re-run job never repeats finished work.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from autoedit_schemas import parse_model
from autoedit_schemas.media import MediaInfo
from autoedit_schemas.signals import Signals

from . import __version__
from .media import make_proxy, probe, sha256_file
from .signals.assemble import assemble
from .signals.audio import AudioAnalysis, analyse_audio
from .signals.scenes import detect_scene_cuts
from .signals.subjects import SubjectTrack, track_subjects
from .signals.video import VideoFrames, analyse_frames

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float], None]
"""Called with (stage, fraction 0..1) as the analysis advances."""

STAGE_WEIGHTS = {"hash": 0.05, "probe": 0.02, "proxy": 0.33, "signals": 0.60}


@dataclass
class ClipAnalysis:
    clip_id: str
    source: Path
    sha256: str
    media: MediaInfo
    proxy_path: Path
    signals_path: Path
    signals: Signals
    cached: bool
    elapsed_s: float


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class _Progress:
    def __init__(self, fn: ProgressFn | None):
        self.fn = fn
        self.done = 0.0

    def stage(self, name: str, fraction: float = 0.0) -> None:
        if self.fn:
            self.fn(name, min(1.0, self.done + STAGE_WEIGHTS.get(name, 0.0) * fraction))

    def finish(self, name: str) -> None:
        self.done += STAGE_WEIGHTS.get(name, 0.0)
        if self.fn:
            self.fn(name, min(1.0, self.done))


def compute_signals(
    proxy: Path, info: MediaInfo, *, clip_id: str, sha256: str, on_step: Callable[[str], None] | None = None
) -> Signals:
    """Run the four independent signal passes concurrently (they are ffmpeg/numpy bound and release
    the GIL), then fuse them."""
    timings: dict[str, float] = {}

    def timed(name: str, fn: Callable[[], object]) -> object:
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            timings[name] = time.perf_counter() - t0
            if on_step:
                on_step(name)

    # AUTOEDIT_SIGNAL_WORKERS=1 runs the passes one after another: slower, but peak memory drops enough
    # for small hosts (e.g. a 512 MB instance).
    workers = max(1, min(4, int(os.environ.get("AUTOEDIT_SIGNAL_WORKERS", "4"))))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="signals") as pool:
        f_video = pool.submit(timed, "video", lambda: analyse_frames(proxy, info))
        f_subjects = pool.submit(timed, "subjects", lambda: track_subjects(proxy, info))
        f_scenes = pool.submit(timed, "scenes", lambda: detect_scene_cuts(proxy, min(info.fps, 30.0)))
        f_audio = (
            pool.submit(timed, "audio", lambda: analyse_audio(proxy, info.duration_s))
            if info.has_audio
            else None
        )
        frames = f_video.result()
        subjects = f_subjects.result()
        cuts = f_scenes.result()
        audio = f_audio.result() if f_audio else None

    assert isinstance(frames, VideoFrames)
    assert isinstance(subjects, SubjectTrack)
    assert isinstance(cuts, list)
    assert audio is None or isinstance(audio, AudioAnalysis)
    return assemble(
        clip_id=clip_id,
        sha256=sha256,
        info=info,
        frames=frames,
        subjects=subjects,
        scene_cuts=cuts,
        audio=audio,
        timings=timings,
        pipeline_version=__version__,
    )


def analyze_clip(
    source: Path,
    cache_root: Path,
    *,
    clip_id: str | None = None,
    sha256: str | None = None,
    progress: ProgressFn | None = None,
    use_cache: bool = True,
) -> ClipAnalysis:
    t0 = time.perf_counter()
    prog = _Progress(progress)

    prog.stage("hash")
    digest = sha256 or sha256_file(source)
    prog.finish("hash")

    workdir = cache_root / digest[:2] / digest
    workdir.mkdir(parents=True, exist_ok=True)
    probe_path = workdir / "probe.json"
    proxy_path = workdir / "proxy.mp4"
    signals_path = workdir / f"signals.v{__version__}.json"
    cid = clip_id or source.stem

    prog.stage("probe")
    if use_cache and probe_path.is_file():
        info = parse_model(MediaInfo, probe_path.read_text())
    else:
        info = probe(source)
        _write_atomic(probe_path, info.model_dump_json(indent=2))
    prog.finish("probe")

    cached = False
    if use_cache and signals_path.is_file() and proxy_path.is_file():
        signals = parse_model(Signals, signals_path.read_text())
        if signals.clip_id != cid:
            signals = signals.model_copy(update={"clip_id": cid})
        cached = True
        prog.finish("proxy")
        prog.finish("signals")
    else:
        prog.stage("proxy")
        if not (use_cache and proxy_path.is_file()):
            make_proxy(source, proxy_path, info, progress=lambda f: prog.stage("proxy", f))
        prog.finish("proxy")

        prog.stage("signals")
        steps_done = 0
        n_steps = 4 if info.has_audio else 3

        def on_step(_name: str) -> None:
            nonlocal steps_done
            steps_done += 1
            prog.stage("signals", steps_done / (n_steps + 1))

        signals = compute_signals(proxy_path, info, clip_id=cid, sha256=digest, on_step=on_step)
        _write_atomic(signals_path, signals.model_dump_json(indent=2))
        prog.finish("signals")

    return ClipAnalysis(
        clip_id=cid,
        source=source,
        sha256=digest,
        media=info,
        proxy_path=proxy_path,
        signals_path=signals_path,
        signals=signals,
        cached=cached,
        elapsed_s=time.perf_counter() - t0,
    )


def default_cache_root() -> Path:
    return Path(os.environ.get("AUTOEDIT_CACHE_DIR", Path.cwd() / ".autoedit-cache"))


def signals_json(signals: Signals) -> str:
    return json.dumps(signals.model_dump(mode="json"), indent=2)
