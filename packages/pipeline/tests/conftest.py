from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autoedit_pipeline.synth import make_storyboard_clip


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def storyboard(media_dir: Path) -> Path:
    """30 s clip with known ground truth (see autoedit_pipeline.synth)."""
    return make_storyboard_clip(media_dir / "storyboard.mp4")


@pytest.fixture(scope="session")
def storyboard_rot90(media_dir: Path) -> Path:
    return make_storyboard_clip(media_dir / "rot90.mp4", rotate=90, audio=False)


@pytest.fixture(scope="session")
def small_webm(media_dir: Path) -> Path:
    dst = media_dir / "small.webm"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=3",
         "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", str(dst)],
        check=True,
    )  # fmt: skip
    return dst


@pytest.fixture(scope="session")
def audio_only(media_dir: Path) -> Path:
    dst = media_dir / "tone.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2",
         "-c:a", "aac", str(dst)],
        check=True,
    )  # fmt: skip
    return dst


@pytest.fixture(scope="session")
def storyboard_analysis(storyboard: Path, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from autoedit_pipeline.analyze import analyze_clip

    return analyze_clip(storyboard, tmp_path_factory.mktemp("cache"), clip_id="c1")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if shutil.which("ffmpeg") is None:  # pragma: no cover
        skip = pytest.mark.skip(reason="ffmpeg not installed")
        for item in items:
            item.add_marker(skip)
