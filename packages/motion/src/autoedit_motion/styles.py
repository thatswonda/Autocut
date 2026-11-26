"""Motion style presets (the "templates" a user picks before sending) and brand palette resolution.

A style sets typography, background treatment, motion personality and default transitions. The brand
kit supplies the colors; the style decides how they are used. Every text/background pairing is checked
for contrast and corrected before anything renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Surface = Literal["primary", "secondary", "accent", "background", "dark", "light"]


@dataclass(frozen=True)
class StylePreset:
    key: str
    name: str
    description: str
    display_font: str
    body_font: str
    display_weight: int
    body_weight: int
    uppercase_headlines: bool
    headline_tracking: float  # em
    background: Literal["solid", "gradient", "shapes", "grid", "spotlight"]
    surfaces: tuple[Surface, ...]  # cycled across scenes
    headline_anim: str
    body_anim: str
    item_anim: str
    transition: Literal["cut", "fade", "wipe", "slide", "zoom"]
    transition_s: float
    radius: float  # in u (1u = 1% of the short edge)
    easing: str
    preview_colors: tuple[str, str, str] = field(default=("#111111", "#ffffff", "#ff5a1f"))


STYLES: dict[str, StylePreset] = {
    s.key: s
    for s in [
        StylePreset(
            key="bold_pop", name="Bold Pop", description="Big condensed type, punchy pops, color-blocked scenes.",
            display_font="Bebas Neue", body_font="Poppins", display_weight=400, body_weight=600,
            uppercase_headlines=True, headline_tracking=0.01, background="shapes",
            surfaces=("primary", "background", "accent", "background"),
            headline_anim="pop_words", body_anim="fade_up", item_anim="slide_left",
            transition="slide", transition_s=0.45, radius=3.0, easing="cubic-bezier(.2,.9,.2,1.15)",
            preview_colors=("#ff3d57", "#fff6e9", "#111111"),
        ),
        StylePreset(
            key="clean_minimal", name="Clean Minimal", description="Lots of white space, crisp sans, calm fades.",
            display_font="Inter", body_font="Inter", display_weight=800, body_weight=400,
            uppercase_headlines=False, headline_tracking=-0.03, background="solid",
            surfaces=("background",),
            headline_anim="fade_up", body_anim="fade_up", item_anim="fade_up",
            transition="fade", transition_s=0.5, radius=2.0, easing="cubic-bezier(.22,.61,.36,1)",
            preview_colors=("#ffffff", "#111111", "#2f6bff"),
        ),
        StylePreset(
            key="luxury", name="Luxury", description="Dark stage, elegant serif, slow reveals.",
            display_font="Playfair Display", body_font="Inter", display_weight=600, body_weight=400,
            uppercase_headlines=False, headline_tracking=-0.01, background="spotlight",
            surfaces=("dark",),
            headline_anim="blur_in", body_anim="fade_up", item_anim="fade_up",
            transition="fade", transition_s=0.8, radius=0.6, easing="cubic-bezier(.25,.1,.25,1)",
            preview_colors=("#0f0d0a", "#f3ead8", "#c9a45c"),
        ),
        StylePreset(
            key="tech_grid", name="Tech Grid", description="Geometric grotesk, grid backdrop, wipe transitions.",
            display_font="Space Grotesk", body_font="Inter", display_weight=700, body_weight=400,
            uppercase_headlines=False, headline_tracking=-0.02, background="grid",
            surfaces=("dark", "primary"),
            headline_anim="mask_reveal", body_anim="fade_up", item_anim="slide_left",
            transition="wipe", transition_s=0.45, radius=1.2, easing="cubic-bezier(.65,0,.35,1)",
            preview_colors=("#0b1020", "#e8ecff", "#36d6ff"),
        ),
        StylePreset(
            key="warm_friendly", name="Warm Friendly", description="Rounded type, soft gradients, bouncy motion.",
            display_font="Poppins", body_font="Poppins", display_weight=800, body_weight=400,
            uppercase_headlines=False, headline_tracking=-0.02, background="gradient",
            surfaces=("background", "secondary"),
            headline_anim="pop_words", body_anim="fade_up", item_anim="pop",
            transition="zoom", transition_s=0.5, radius=4.0, easing="cubic-bezier(.34,1.4,.64,1)",
            preview_colors=("#fff1e6", "#3b2418", "#ff8a3d"),
        ),
        StylePreset(
            key="editorial", name="Editorial", description="Magazine serif headlines, clean grid, crisp wipes.",
            display_font="DM Serif Display", body_font="Inter", display_weight=400, body_weight=400,
            uppercase_headlines=False, headline_tracking=-0.01, background="solid",
            surfaces=("light", "background"),
            headline_anim="mask_reveal", body_anim="fade_up", item_anim="fade_up",
            transition="wipe", transition_s=0.5, radius=0.0, easing="cubic-bezier(.77,0,.18,1)",
            preview_colors=("#f6f1e8", "#1d1a16", "#b3412c"),
        ),
    ]
}

DEFAULT_STYLE = "bold_pop"


def get_style(key: str | None) -> StylePreset:
    return STYLES.get(key or "", STYLES[DEFAULT_STYLE])


# ----------------------------------------------------------------------------------------- color


def _hex(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")[:6]
    return tuple(int(c[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in rgb)


def luminance(c: str) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _hex(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = _hex(a), _hex(b)
    return _to_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb, strict=True)))  # type: ignore[arg-type]


def readable_on(bg: str, preferred: str, min_ratio: float = 4.5) -> str:
    """``preferred`` if it reads on ``bg``, otherwise the better of near-white / near-black."""
    if contrast(bg, preferred) >= min_ratio:
        return preferred
    light, dark = "#fafafa", "#121212"
    return light if contrast(bg, light) >= contrast(bg, dark) else dark


@dataclass(frozen=True)
class Surfaces:
    bg: str
    bg2: str  # secondary tone for gradients / shapes
    ink: str  # main text
    muted: str  # secondary text
    accent: str  # highlights, bars, buttons
    accent_ink: str  # text on accent


def resolve_surface(kind: Surface, colors: dict[str, str]) -> Surfaces:
    primary = colors.get("primary", "#111111")
    secondary = colors.get("secondary", "#444444")
    accent = colors.get("accent", "#ff5a1f")
    background = colors.get("background", "#ffffff")
    text = colors.get("text", "#111111")
    if kind == "dark":
        bg = mix("#0c0c0e", primary, 0.10)
    elif kind == "light":
        bg = mix("#fbfaf7", primary, 0.04)
    elif kind == "primary":
        bg = primary
    elif kind == "secondary":
        bg = secondary
    elif kind == "accent":
        bg = accent
    else:
        bg = background
    ink = readable_on(bg, text)
    # The accent must stand out from this surface; fall back to primary, then to ink.
    acc = accent
    if contrast(bg, acc) < 2.2:
        acc = primary if contrast(bg, primary) >= 2.2 else ink
    bg2 = mix(bg, acc, 0.35) if kind in ("primary", "accent", "secondary") else mix(bg, primary, 0.18)
    muted = mix(ink, bg, 0.28)
    return Surfaces(bg=bg, bg2=bg2, ink=ink, muted=muted, accent=acc, accent_ink=readable_on(acc, bg))
