"""Color grade presets shown in the app. The render uses the 3D LUT looks in ``looks.py``; each preset
here carries a CSS filter that approximates the look for instant previews in the browser."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorPreset:
    key: str
    name: str
    description: str
    css: str


COLOR_PRESETS: dict[str, ColorPreset] = {
    p.key: p
    for p in [
        ColorPreset("none", "Original", "No look, only automatic exposure and white balance.", "none"),
        ColorPreset("natural", "Natural", "Clean, true-to-life color with gentle contrast.",
                    "contrast(1.08) saturate(1.08)"),
        ColorPreset("warm_cinematic", "Warm Cinematic", "Golden highlights, deep shadows, soft vignette.",
                    "sepia(0.22) saturate(1.05) contrast(1.15) brightness(0.97)"),
        ColorPreset("teal_orange", "Teal & Orange", "Blockbuster look: teal shadows and tones, warm skin.",
                    "contrast(1.18) saturate(1.15) sepia(0.12) hue-rotate(-8deg)"),
        ColorPreset("moody_film", "Moody Film", "Dark, muted and cinematic, with film grain.",
                    "brightness(0.82) contrast(1.22) saturate(0.68) sepia(0.08)"),
        ColorPreset("clean_bright", "Clean & Bright", "Airy, bright and crisp. Great for products.",
                    "brightness(1.10) contrast(1.05) saturate(1.12)"),
        ColorPreset("vintage", "Vintage", "Faded film warmth with lifted blacks and grain.",
                    "sepia(0.38) contrast(0.92) saturate(0.82) brightness(1.03)"),
        ColorPreset("black_white", "Black & White", "Rich monochrome with punchy contrast.",
                    "grayscale(1) contrast(1.3)"),
        ColorPreset("vibrant_pop", "Vibrant Pop", "Bold, saturated color for social feeds.",
                    "saturate(1.45) contrast(1.12)"),
    ]
}  # fmt: skip


def get_preset(key: str | None) -> ColorPreset:
    return COLOR_PRESETS.get(key or "", COLOR_PRESETS["natural"])
