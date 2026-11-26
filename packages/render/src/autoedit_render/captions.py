"""Word-by-word burned-in captions (PRD 5.4): EDL caption words -> an ASS script rendered by libass.

Words are grouped into short lines that never cross a sentence end or a long gap. Styles:
* ``word_pop``  - the line stays up, the word being spoken pops in the accent color (social default);
* ``bold_box``  - white text on a dark box, current word highlighted;
* ``clean``     - simple white text with a soft outline, no per-word highlight;
* ``karaoke``   - uppercase heavy type, current word in the accent color.
Captions sit in the lower third, clear of the bottom UI of TikTok/Reels/Shorts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from autoedit_schemas.edl import CaptionWord

FONTS_DIR = Path(__file__).parent / "fonts"
STYLES = ("word_pop", "bold_box", "clean", "karaoke")
_RTL = re.compile(r"[֐-ࣿ]")


@dataclass(frozen=True)
class _Look:
    font: str
    size: float  # fraction of the output height
    upper: bool
    outline: float
    box: bool
    highlight: bool
    pop: bool


LOOKS = {
    "word_pop": _Look("Poppins ExtraBold", 0.068, False, 0.0042, False, True, True),
    "bold_box": _Look("Inter ExtraBold", 0.056, False, 0.0, True, True, False),
    "clean": _Look("Inter SemiBold", 0.050, False, 0.0030, False, False, False),
    "karaoke": _Look("Bebas Neue", 0.086, True, 0.0045, False, True, True),
}


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words: list[CaptionWord], *, max_words: int, max_chars: int) -> list[list[CaptionWord]]:
    groups: list[list[CaptionWord]] = []
    cur: list[CaptionWord] = []
    for w in words:
        if cur:
            text = " ".join(x.text for x in [*cur, w])
            gap = w.t_in - cur[-1].t_out
            ends = cur[-1].text.rstrip().endswith((".", "!", "?", "…", ":"))
            if len(cur) >= max_words or len(text) > max_chars or gap > 0.6 or ends:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[CaptionWord], *, width: int, height: int, style: str = "word_pop",
              accent: str = "#FFD400", top: bool = False) -> str:  # fmt: skip
    look = LOOKS.get(style, LOOKS["word_pop"])
    size = round(height * look.size if width < height else height * look.size * 1.25)
    vertical = width < height
    max_chars = 18 if vertical else 32
    max_words = 3 if vertical else 6
    outline = max(2, round(height * look.outline)) if look.outline else 0
    margin_v = round(height * (0.07 if top else 0.24 if vertical else 0.10))
    align = 8 if top else 2
    margin_h = round(width * 0.08)
    border_style = 3 if look.box else 1
    back = _ass_color("#000000", 0x50) if look.box else _ass_color("#000000", 0x80)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{look.font},{size},{_ass_color("#FFFFFF")},{_ass_color(accent)},{_ass_color("#000000", 0x10)},{back},-1,0,0,0,100,100,0,0,{border_style},{outline if not look.box else round(size * 0.22)},{0 if look.box else round(size * 0.06)},{align},{margin_h},{margin_h},{margin_v},1
Style: Rtl,Noto Sans Arabic Bold,{size},{_ass_color("#FFFFFF")},{_ass_color(accent)},{_ass_color("#000000", 0x10)},{back},-1,0,0,0,100,100,0,0,{border_style},{outline or 2},0,{align},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    hi = _ass_color(accent)
    lines: list[str] = []
    for grp in group_words(words, max_words=max_words, max_chars=max_chars):
        texts = [_escape(w.text.upper() if look.upper else w.text) for w in grp]
        rtl = any(_RTL.search(t) for t in texts)
        st = "Rtl" if rtl else "Cap"
        g_in, g_out = grp[0].t_in, max(grp[-1].t_out, grp[0].t_in + 0.25)
        if not look.highlight or rtl:
            lines.append(f"Dialogue: 0,{_ass_time(g_in)},{_ass_time(g_out)},{st},,0,0,0,,{' '.join(texts)}")
            continue
        for i, w in enumerate(grp):
            a = g_in if i == 0 else w.t_in
            b = grp[i + 1].t_in if i + 1 < len(grp) else g_out
            if b - a < 0.02:
                continue
            parts = []
            for j, t in enumerate(texts):
                if j == i:
                    pop = r"\fscx112\fscy112" if look.pop else ""
                    tag = r"\t(0,90,\fscx100\fscy100)" if look.pop else ""
                    parts.append(f"{{\\c{hi}{pop}{tag}}}{t}{{\\r}}")
                elif grp[j].emphasis:
                    parts.append(f"{{\\c{hi}}}{t}{{\\r}}")
                else:
                    parts.append(t)
            lines.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},{st},,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


def write_ass(words: list[CaptionWord], path: Path, *, width: int, height: int, style: str,
              accent: str = "#FFD400", top: bool = False) -> Path:  # fmt: skip
    path.write_text(
        build_ass(words, width=width, height=height, style=style, accent=accent, top=top), encoding="utf-8"
    )
    return path


def subtitles_filter(ass_path: Path) -> str:
    """FFmpeg filter that burns ``ass_path`` using the bundled fonts."""

    def esc(p: Path) -> str:
        return str(p.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    return f"ass='{esc(ass_path)}':fontsdir='{esc(FONTS_DIR)}'"


def srt(words: list[CaptionWord]) -> str:
    """SubRip captions (download alongside the video)."""

    def ts(t: float) -> str:
        ms = round(max(0.0, t) * 1000)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for i, grp in enumerate(group_words(words, max_words=7, max_chars=42), start=1):
        out.append(f"{i}\n{ts(grp[0].t_in)} --> {ts(grp[-1].t_out)}\n{' '.join(w.text for w in grp)}\n")
    return "\n".join(out)
