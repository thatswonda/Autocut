from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from autoedit_motion.page import build_page, output_size
from autoedit_motion.render import render_composition, snapshot
from autoedit_motion.styles import STYLES, contrast, resolve_surface
from autoedit_pipeline.media import probe
from autoedit_schemas import Composition, parse_model

CHROMIUM = os.environ.get("AUTOEDIT_CHROMIUM_PATH") or shutil.which("chromium")
needs_browser = pytest.mark.skipif(not CHROMIUM and not os.environ.get("CI"), reason="no Chromium available")


def comp(aspect: str = "9:16", style: str = "bold_pop", language: str = "en") -> Composition:
    return parse_model(Composition, {
        "meta": {"aspect": aspect, "duration_s": 4, "fps": 30, "style": style, "language": language},
        "brand": {"name": "Kora", "logo_asset": "logo", "colors": {"primary": "#E85D04", "accent": "#FFB703"}},
        "scenes": [
            {"id": "s1", "template": "kinetic_text", "t_in": 0, "t_out": 2,
             "content": {"headline": "A very long headline that must shrink to fit the frame nicely"},
             "transition_out": {"type": "wipe", "duration_s": 0.4}},
            {"id": "s2", "template": "cta_end_card", "t_in": 2, "t_out": 4,
             "content": {"headline": "Order now", "cta": "Order"}},
        ],
    })  # fmt: skip


def test_output_sizes() -> None:
    assert output_size("9:16", 720) == (720, 1280)
    assert output_size("16:9", 1080) == (1920, 1080)
    assert output_size("4:5", 720) == (720, 900)


def test_every_style_reads() -> None:
    for st in STYLES.values():
        for kind in st.surfaces:
            surf = resolve_surface(kind, {"primary": "#FFE600", "background": "#FFFFFF", "text": "#FFFFFF"})
            assert contrast(surf.bg, surf.ink) >= 4.5, (st.key, kind)


def test_page_escapes_text_and_sets_rtl() -> None:
    c = comp(language="ar")
    data = c.model_dump(by_alias=True)
    data["scenes"][0]["content"]["headline"] = "<script>alert(1)</script> قهوة"
    html = build_page(parse_model(Composition, data), {})
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
    assert 'dir="rtl"' in html


@needs_browser
def test_renders_mp4(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (300, 100), (232, 93, 4, 255)).save(logo)
    res = render_composition(
        comp(), {"logo": logo}, tmp_path / "out.mp4", workdir=tmp_path / "w", short_edge=360
    )
    info = probe(res.path)
    assert (info.display_width, info.display_height) == (360, 640)
    assert info.duration_s == pytest.approx(4.0, abs=0.1)
    assert info.has_audio
    assert res.warnings == []  # the long headline was fitted, not clipped


@needs_browser
def test_snapshot_changes_over_time(tmp_path: Path) -> None:
    a, b = snapshot(comp(), {}, [0.0, 1.5], workdir=tmp_path / "s", short_edge=180)
    assert a != b  # animation actually moves
