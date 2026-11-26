"""Scene-change detection with PySceneDetect's adaptive content detector."""

from __future__ import annotations

import logging
from pathlib import Path

from scenedetect import AdaptiveDetector, SceneManager, open_video

log = logging.getLogger(__name__)

MIN_SCENE_LEN_S = 0.5


def detect_scene_cuts(proxy: Path, fps: float) -> list[float]:
    """Return the timestamps (seconds) of hard cuts. The proxy is already small, so no downscale."""
    video = open_video(str(proxy), backend="opencv")
    manager = SceneManager()
    manager.auto_downscale = False
    manager.downscale = 2
    manager.add_detector(AdaptiveDetector(min_scene_len=max(2, round(MIN_SCENE_LEN_S * fps))))
    manager.detect_scenes(video=video, show_progress=False)
    scenes = manager.get_scene_list(start_in_scene=True)
    # Every scene after the first starts at a cut.
    return [round(_seconds(start), 3) for start, _end in scenes[1:]]


def _seconds(tc: object) -> float:
    value = getattr(tc, "seconds", None)
    if value is None:
        value = tc.get_seconds()  # type: ignore[attr-defined]
    return float(value)
