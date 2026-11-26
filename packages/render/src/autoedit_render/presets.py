"""Color grade presets: an FFmpeg filter chain for the render, and a CSS filter that approximates the
look for instant previews in the browser before anything renders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorPreset:
    key: str
    name: str
    description: str
    ffmpeg: str
    css: str


COLOR_PRESETS: dict[str, ColorPreset] = {
    p.key: p
    for p in [
        ColorPreset("none", "Original", "No grade, exposure fixes only.", "null", "none"),
        ColorPreset("natural", "Natural", "Gentle contrast and color, true to life.",
                    "eq=contrast=1.05:saturation=1.08", "contrast(1.05) saturate(1.08)"),
        ColorPreset("warm_cinematic", "Warm Cinematic", "Golden highlights, rich shadows, soft vignette.",
                    "colorbalance=rs=0.05:bs=-0.06:rm=0.04:bm=-0.04:rh=0.04:bh=-0.04,eq=contrast=1.08:saturation=1.08,vignette=PI/5",
                    "sepia(0.18) saturate(1.12) contrast(1.08)"),
        ColorPreset("teal_orange", "Teal & Orange", "Blockbuster look: teal shadows, warm skin.",
                    "colorbalance=rs=-0.10:gs=-0.02:bs=0.10:rh=0.08:gh=0.01:bh=-0.08,eq=contrast=1.1:saturation=1.15",
                    "contrast(1.1) saturate(1.2) sepia(0.1) hue-rotate(-6deg)"),
        ColorPreset("moody_film", "Moody Film", "Low-key, muted color, film grain.",
                    "eq=contrast=1.12:brightness=-0.03:saturation=0.82,colorbalance=bs=0.05:rh=0.03,noise=alls=6:allf=t,vignette=PI/4",
                    "contrast(1.12) brightness(0.94) saturate(0.8)"),
        ColorPreset("clean_bright", "Clean & Bright", "Airy, bright and crisp. Great for products.",
                    "eq=brightness=0.035:contrast=1.04:saturation=1.1,unsharp=5:5:0.4",
                    "brightness(1.07) contrast(1.04) saturate(1.1)"),
        ColorPreset("vintage", "Vintage", "Faded warmth of old film.",
                    "curves=preset=vintage,eq=saturation=0.9,vignette=PI/4.5",
                    "sepia(0.35) contrast(0.95) saturate(0.85)"),
        ColorPreset("black_white", "Black & White", "Classic monochrome with punch.",
                    "hue=s=0,eq=contrast=1.18:brightness=0.01", "grayscale(1) contrast(1.18)"),
        ColorPreset("vibrant_pop", "Vibrant Pop", "Bold saturated color for social feeds.",
                    "eq=saturation=1.35:contrast=1.08,unsharp=5:5:0.5", "saturate(1.4) contrast(1.08)"),
    ]
}  # fmt: skip


def get_preset(key: str | None) -> ColorPreset:
    return COLOR_PRESETS.get(key or "", COLOR_PRESETS["natural"])
