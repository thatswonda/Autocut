"""Ground-truth checks against the synthetic storyboard clip (see autoedit_pipeline.synth)."""

from __future__ import annotations

import itertools
import time
from pathlib import Path
from typing import Any

import pytest

from autoedit_pipeline.analyze import analyze_clip
from autoedit_schemas import Signals, parse_model

pytestmark = pytest.mark.slow


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def covered(ranges: list[Any], target: tuple[float, float]) -> float:
    """Share of ``target`` covered by ``ranges``."""
    return sum(overlap((r.t_in, r.t_out), target) for r in ranges) / (target[1] - target[0])


def test_validates_and_round_trips(storyboard_analysis: Any) -> None:
    signals = storyboard_analysis.signals
    again = parse_model(Signals, Path(storyboard_analysis.signals_path).read_text())
    assert again == signals
    assert len(signals.per_second) == 30
    assert signals.clip_id == "c1"


def test_scene_cuts(storyboard_analysis: Any) -> None:
    cuts = storyboard_analysis.signals.scene_cuts
    for expected in (3.0, 10.0, 14.0, 24.0):
        assert any(abs(c - expected) < 0.2 for c in cuts), (expected, cuts)
    assert len(storyboard_analysis.signals.scenes) == len(cuts) + 1


def test_black_and_dead_zones(storyboard_analysis: Any) -> None:
    s = storyboard_analysis.signals
    zones = {(round(z.t_in), round(z.t_out)): z.reason for z in s.dead_zones}
    assert zones.get((0, 3)) == "black_frames"
    static = [z for z in s.dead_zones if z.reason == "static_empty"]
    assert static and covered(static, (10.6, 13.6)) > 0.9
    # The talking parts are never dead.
    assert covered(s.dead_zones, (3.5, 9.5)) == 0
    assert covered(s.dead_zones, (24.5, 29.5)) == 0
    assert all(r.black for r in s.per_second[:3])


def test_speech_music_silence(storyboard_analysis: Any) -> None:
    s = storyboard_analysis.signals
    assert covered(s.speech_ranges, (3.5, 9.0)) > 0.85
    assert covered(s.speech_ranges, (24.5, 27.5)) > 0.85
    assert covered(s.speech_ranges, (14.5, 22.5)) < 0.05  # VAD alone fires on music; YAMNet gate stops it
    assert covered(s.music_ranges, (14.5, 23.5)) > 0.9
    assert covered(s.music_ranges, (3.0, 10.0)) == 0
    assert covered(s.silences, (0.2, 2.8)) > 0.9
    assert covered(s.silences, (10.5, 13.5)) > 0.9
    assert s.summary.audio is not None
    assert s.summary.audio.integrated_lufs is not None


def test_beats_follow_music(storyboard_analysis: Any) -> None:
    s = storyboard_analysis.signals
    assert s.summary.audio.tempo_bpm == pytest.approx(120, rel=0.08)
    assert len(s.beats) >= 15
    assert all(13.0 <= b <= 25.0 for b in s.beats)


def test_shake_only_on_handheld_section(storyboard_analysis: Any) -> None:
    s = storyboard_analysis.signals
    shake = [f for f in s.quality_flags if f.reason == "shake"]
    assert covered(shake, (24.5, 29.5)) > 0.9
    assert covered(shake, (3.0, 23.0)) == 0  # moving content is not camera shake
    assert not any(f.reason == "blur" for f in s.quality_flags)


def test_cache_hit_is_fast(storyboard: Path, storyboard_analysis: Any) -> None:
    cache_root = Path(storyboard_analysis.proxy_path).parents[2]
    t0 = time.perf_counter()
    again = analyze_clip(storyboard, cache_root, clip_id="c9", sha256=storyboard_analysis.sha256)
    assert again.cached
    assert again.signals.clip_id == "c9"
    assert time.perf_counter() - t0 < 2.0


def test_silent_clip_has_no_audio_fields(storyboard_rot90: Path, tmp_path: Path) -> None:
    result = analyze_clip(storyboard_rot90, tmp_path, clip_id="r")
    s = result.signals
    assert s.media.rotation == 90
    assert s.summary.audio is None
    assert all(r.speech is None and r.loudness_lufs is None for r in s.per_second)
    assert s.speech_ranges == [] and s.beats == []
    # Video signals survive the rotation.
    assert any(abs(c - 10.0) < 0.2 for c in s.scene_cuts)
    assert any(z.reason == "black_frames" for z in s.dead_zones)


def test_progress_reports_monotonic(storyboard: Path, tmp_path: Path) -> None:
    seen: list[float] = []
    analyze_clip(storyboard, tmp_path, progress=lambda _stage, f: seen.append(f))
    assert seen[-1] == pytest.approx(1.0)
    assert all(b >= a - 1e-9 for a, b in itertools.pairwise(seen))


def test_models_are_world_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: models baked as root must be readable by the unprivileged worker user."""
    import stat

    from autoedit_pipeline import models

    src = models.models_dir() / models.MODELS["face"].filename
    if not src.is_file():
        pytest.skip("face model not downloaded")
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda *_a, **_k: src.open("rb"))
    dest = tmp_path / "m.tflite"
    models.fetch(models.MODELS["face"], dest)
    assert stat.S_IMODE(dest.stat().st_mode) & 0o044 == 0o044


def test_subjects_survive_missing_detectors(
    storyboard_rot90: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If neither MediaPipe nor OpenCV detectors load, analysis still succeeds without subject data."""
    from autoedit_pipeline.signals import subjects

    monkeypatch.setattr(subjects, "model_path", lambda _name: None)

    def broken() -> None:
        raise RuntimeError("no cv2 contrib")

    monkeypatch.setattr(subjects, "_OpenCVDetectors", broken)
    result = analyze_clip(storyboard_rot90, tmp_path, clip_id="nodet", use_cache=False)
    assert result.signals.analysis.detectors["faces"] == "none"
    assert result.signals.summary.video.face_ratio == 0
