from __future__ import annotations

from pathlib import Path

import pytest

from autoedit_pipeline.media import MediaError, frame_spec, iter_frames, make_proxy, probe, proxy_dimensions


def test_probe_storyboard(storyboard: Path) -> None:
    info = probe(storyboard)
    assert info.container == "mp4"
    assert (info.width, info.height, info.rotation) == (1280, 720, 0)
    assert info.duration_s == pytest.approx(30.0, abs=0.1)
    assert info.fps == pytest.approx(30.0)
    assert info.has_audio and info.audio_codec == "aac"
    assert info.orientation == "landscape"


def test_probe_rotation_swaps_display_size(storyboard_rot90: Path) -> None:
    info = probe(storyboard_rot90)
    assert info.rotation == 90
    assert (info.display_width, info.display_height) == (720, 1280)
    assert info.orientation == "portrait"
    assert not info.has_audio
    assert proxy_dimensions(info) == (540, 960)


def test_probe_webm(small_webm: Path) -> None:
    info = probe(small_webm)
    assert info.container == "webm"
    assert info.video_codec == "vp9"


def test_probe_rejects_audio_only(audio_only: Path) -> None:
    with pytest.raises(MediaError) as exc:
        probe(audio_only)
    assert exc.value.code == "no_video"


def test_probe_rejects_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"this is not a video" * 100)
    with pytest.raises(MediaError) as exc:
        probe(bad)
    assert exc.value.code in {"probe_failed", "unsupported_format"}


def test_probe_missing(tmp_path: Path) -> None:
    with pytest.raises(MediaError) as exc:
        probe(tmp_path / "nope.mp4")
    assert exc.value.code == "not_found"


def test_proxy_is_upright_540p(storyboard_rot90: Path, tmp_path: Path) -> None:
    info = probe(storyboard_rot90)
    proxy = make_proxy(storyboard_rot90, tmp_path / "proxy.mp4", info)
    pinfo = probe(proxy)
    assert (pinfo.display_width, pinfo.display_height) == (540, 960)
    assert pinfo.rotation == 0
    assert pinfo.duration_s == pytest.approx(30.0, abs=0.1)


def test_iter_frames_count_and_shape(storyboard: Path) -> None:
    spec = frame_spec(probe(storyboard), fps=2.0, width=160)
    frames = list(iter_frames(storyboard, spec, gray=True))
    assert len(frames) == 60
    assert frames[0][1].shape == (90, 160)
    assert frames[10][0] == pytest.approx(5.0)
