"""Color looks as 3D LUTs (the way colorists grade), generated from a few readable parameters.

Each look is applied after a per-shot auto-correct (exposure and white balance), so it starts from a
neutral image. A look combines:
* a filmic tone curve (contrast around mid-grey, soft highlight roll-off, optional lifted "film" blacks);
* split toning: a hue pushed into the shadows and another into the highlights, weighted by luminance;
* saturation by hue band with skin protected (skin tones keep their warmth when everything else moves);
* teal & orange style hue bending, black & white channel mixing.
Grain and vignette, which a LUT cannot express, are returned as extra FFmpeg filters.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

LUT_SIZE = 33
Arr = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Look:
    exposure: float = 1.0  # linear gain before the curve (0.8 = about a third of a stop darker)
    neutral_tint: tuple[float, float] = (0.0, 0.0)  # tint for low-saturation mid-tones (floors, walls, sky)
    contrast: float = 0.0  # S-curve strength around mid-grey (0..0.6)
    pivot: float = 0.45
    gamma: float = 1.0  # < 1 brightens mid-tones
    fade: float = 0.0  # lifted blacks (film)
    rolloff: float = 0.85  # where highlights start compressing
    saturation: float = 1.0
    vibrance: float = 0.0  # boosts low-saturation colors more than saturated ones
    shadow_tint: tuple[float, float] = (0.0, 0.0)  # (hue degrees, strength)
    highlight_tint: tuple[float, float] = (0.0, 0.0)
    skin_protect: float = 0.6  # 0..1 how much skin keeps its own saturation/hue
    green_sat: float = 1.0  # saturation multiplier for foliage greens
    teal_orange: float = 0.0  # bend hues toward orange (warm) and teal (everything else)
    warmth: float = 0.0  # global white-balance shift (-1 cool .. 1 warm)
    mono: bool = False
    mono_mix: tuple[float, float, float] = (0.30, 0.59, 0.11)
    grain: float = 0.0  # extra filter, 0..20
    vignette: float = 0.0  # extra filter, 0..1
    sharpen: float = 0.0  # extra filter, 0..1
    extras: tuple[str, ...] = field(default_factory=tuple)


LOOKS: dict[str, Look] = {
    "none": Look(),
    "natural": Look(contrast=0.18, saturation=1.06, vibrance=0.15, warmth=0.05, rolloff=0.88, sharpen=0.25),
    "warm_cinematic": Look(
        contrast=0.42,
        pivot=0.42,
        gamma=1.05,
        fade=0.02,
        saturation=1.0,
        vibrance=0.05,
        shadow_tint=(195, 0.10),
        highlight_tint=(36, 0.20),
        green_sat=0.7,
        warmth=0.25,
        rolloff=0.80,
        vignette=0.45,
        grain=4,
        sharpen=0.2,
    ),
    "teal_orange": Look(
        neutral_tint=(188, 0.16),
        contrast=0.45,
        pivot=0.44,
        gamma=1.06,
        fade=0.015,
        saturation=1.12,
        teal_orange=0.6,
        shadow_tint=(188, 0.24),
        highlight_tint=(28, 0.16),
        green_sat=0.5,
        rolloff=0.80,
        vignette=0.35,
        sharpen=0.25,
    ),
    "moody_film": Look(
        exposure=0.80,
        neutral_tint=(170, 0.07),
        contrast=0.55,
        pivot=0.40,
        gamma=1.28,
        fade=0.035,
        saturation=0.68,
        vibrance=-0.05,
        shadow_tint=(178, 0.20),
        highlight_tint=(34, 0.08),
        green_sat=0.5,
        warmth=-0.06,
        rolloff=0.74,
        vignette=0.7,
        grain=9,
        sharpen=0.1,
    ),
    "clean_bright": Look(
        contrast=0.16,
        gamma=0.80,
        saturation=1.10,
        vibrance=0.2,
        warmth=0.02,
        rolloff=0.84,
        highlight_tint=(200, 0.03),
        sharpen=0.35,
    ),
    "vintage": Look(
        contrast=0.22,
        gamma=1.0,
        fade=0.12,
        saturation=0.78,
        shadow_tint=(285, 0.14),
        highlight_tint=(48, 0.22),
        green_sat=0.65,
        warmth=0.2,
        rolloff=0.76,
        vignette=0.55,
        grain=12,
    ),
    "black_white": Look(
        contrast=0.6,
        pivot=0.44,
        gamma=1.08,
        fade=0.02,
        mono=True,
        mono_mix=(0.45, 0.45, 0.10),
        rolloff=0.82,
        vignette=0.5,
        grain=8,
        sharpen=0.35,
    ),
    "vibrant_pop": Look(contrast=0.3, saturation=1.28, vibrance=0.4, warmth=0.05, rolloff=0.86, sharpen=0.4),
}


def _smooth(x: Arr) -> Arr:
    return x * x * (3 - 2 * x)


def _tone(y: Arr, lk: Look) -> Arr:
    """Filmic tone curve on luminance-like values in [0, 1]: gamma, a logistic S-curve around the pivot
    (blended by ``contrast``), soft highlight roll-off and lifted blacks."""
    y = np.clip(y, 0, 1) ** lk.gamma
    if lk.contrast:
        k = 7.0
        s0 = 1 / (1 + np.exp(k * lk.pivot))
        s1 = 1 / (1 + np.exp(-k * (1 - lk.pivot)))
        sig = (1 / (1 + np.exp(-k * (y - lk.pivot))) - s0) / (s1 - s0)
        y = (1 - lk.contrast) * y + lk.contrast * sig
    if lk.rolloff < 1:
        kk = lk.rolloff
        y = np.where(y > kk, kk + (1 - kk) * np.tanh((y - kk) / (1 - kk) * 1.1) / np.tanh(1.1), y)
    if lk.fade:
        y = lk.fade + y * (1 - lk.fade * 0.7)
    return np.clip(y, 0, 1)


def _hue_rgb(deg: float) -> Arr:
    r, g, b = colorsys.hsv_to_rgb((deg % 360) / 360, 1.0, 1.0)
    return np.array([r, g, b]) - (r + g + b) / 3


def apply_look(rgb: Arr, lk: Look) -> Arr:
    """Apply a look to an (..., 3) array of RGB values in [0, 1]."""
    rgb = np.clip(rgb * lk.exposure, 0, 1).astype(np.float64)
    if lk.warmth:
        rgb = rgb * np.array([1 + 0.06 * lk.warmth, 1 + 0.01 * lk.warmth, 1 - 0.07 * lk.warmth])
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722])
    if lk.mono:
        m = np.array(lk.mono_mix) / sum(lk.mono_mix)
        y = _tone(rgb @ m, lk)
        out = np.repeat(y[..., None], 3, axis=-1)
        if lk.highlight_tint[1] or lk.shadow_tint[1]:
            out = out + _tints(y, lk)
        return np.clip(out, 0, 1)

    # Hue/saturation work in HSV (vectorised).
    mx, mn = rgb.max(-1), rgb.min(-1)
    delta = mx - mn
    sat = np.where(mx > 1e-6, delta / np.maximum(mx, 1e-6), 0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = np.zeros_like(mx)
    nz = delta > 1e-6
    rc = np.where(nz, (mx - r) / np.maximum(delta, 1e-6), 0)
    gc = np.where(nz, (mx - g) / np.maximum(delta, 1e-6), 0)
    bc = np.where(nz, (mx - b) / np.maximum(delta, 1e-6), 0)
    hue = np.where(r == mx, bc - gc, np.where(g == mx, 2.0 + rc - bc, 4.0 + gc - rc))
    hue = (hue / 6.0) % 1.0 * 360

    # Skin: hue ~10-45 deg with moderate saturation.
    skin = (
        np.exp(-(((hue - 27) / 16) ** 2))
        * np.clip((sat - 0.12) / 0.2, 0, 1)
        * np.clip((0.75 - sat) / 0.2, 0, 1)
    )
    skin_w = skin * lk.skin_protect
    green = np.exp(-(((hue - 105) / 35) ** 2))

    s_mult = lk.saturation * (1 + lk.vibrance * (1 - sat))
    s_mult = s_mult * (1 - green * (1 - lk.green_sat))
    s_mult = skin_w * np.maximum(1.0, np.minimum(s_mult, 1.08)) + (1 - skin_w) * s_mult

    if lk.teal_orange:
        warm_target, cool_target = 28.0, 190.0
        d_warm = ((hue - warm_target + 180) % 360) - 180
        d_cool = ((hue - cool_target + 180) % 360) - 180
        to_warm = np.abs(d_warm) < 70
        pull = lk.teal_orange * np.clip(sat * 2.2, 0, 1)
        hue = np.where(to_warm, hue - d_warm * pull * 0.6, hue - d_cool * pull * 0.5) % 360
        hue = np.where(skin > 0.3, hue * (1 - skin_w * 0.0), hue)

    new_sat = np.clip(sat * s_mult, 0, 1)
    v = mx
    # Back to RGB.
    hh = hue / 60.0
    i = np.floor(hh).astype(int) % 6
    f = hh - np.floor(hh)
    p = v * (1 - new_sat)
    q = v * (1 - new_sat * f)
    t = v * (1 - new_sat * (1 - f))
    choices = [np.stack(c, -1) for c in ((v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q))]
    out = np.zeros_like(rgb)
    for k in range(6):
        out = np.where((i == k)[..., None], choices[k], out)

    # Tone on luminance, keeping chroma ratios (no hue shifts from the curve).
    y_old = np.maximum(out @ np.array([0.2126, 0.7152, 0.0722]), 1e-5)
    y_new = _tone(y_old, lk)
    out = out * (y_new / y_old)[..., None]
    # Re-saturate slightly where the curve compressed highlights, then split-tone.
    out = out + _tints(y_new, lk)
    if lk.neutral_tint[1]:
        mids = np.clip(1 - np.abs(y_new - 0.5) / 0.45, 0, 1)
        neutral = np.clip(1 - new_sat * 3.0, 0, 1)
        out = out + (mids * neutral)[..., None] * _hue_rgb(lk.neutral_tint[0]) * lk.neutral_tint[1]
    del luma
    return np.clip(out, 0, 1)


def _tints(y: Arr, lk: Look) -> Arr:
    sh_w = np.clip(1 - y / 0.5, 0, 1) ** 1.5
    hi_w = np.clip((y - 0.5) / 0.5, 0, 1) ** 1.2
    add = np.zeros((*y.shape, 3))
    if lk.shadow_tint[1]:
        add += sh_w[..., None] * _hue_rgb(lk.shadow_tint[0]) * lk.shadow_tint[1]
    if lk.highlight_tint[1]:
        add += hi_w[..., None] * _hue_rgb(lk.highlight_tint[0]) * lk.highlight_tint[1]
    return add


def _cache_dir() -> Path:
    root = os.environ.get("AUTOEDIT_LUT_DIR") or str(Path(tempfile.gettempdir()) / "autoedit-luts")
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def lut_file(key: str, *, strength: float = 1.0) -> Path | None:
    """Path of a .cube file for the look (cached by its parameters). None for no grade."""
    lk = LOOKS.get(key)
    if lk is None or key == "none":
        return None
    strength = max(0.0, min(1.0, strength))
    digest = hashlib.sha1(json.dumps([asdict(lk), strength, LUT_SIZE], default=str).encode()).hexdigest()[:12]
    path = _cache_dir() / f"{key}-{digest}.cube"
    if path.is_file():
        return path
    n = LUT_SIZE
    grid = np.linspace(0, 1, n)
    # .cube order: red changes fastest.
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], -1).reshape(-1, 3)
    graded = apply_look(rgb, lk)
    graded = rgb + (graded - rgb) * strength
    lines = [f'TITLE "Autocut {key}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in graded]
    tmp = path.with_suffix(".part")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)
    return path


def look_filters(key: str, *, height: int, strength: float = 1.0, with_grain: bool = True) -> str:
    """FFmpeg filters for the look: LUT, then vignette, grain and gentle sharpening."""
    lk = LOOKS.get(key, LOOKS["natural"])
    parts: list[str] = []
    path = lut_file(key, strength=strength)
    if path is not None:
        esc = str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        parts.append(f"format=rgb24,lut3d=file='{esc}':interp=tetrahedral,format=yuv420p")
    if lk.vignette:
        parts.append(f"vignette=angle={0.25 + 0.35 * lk.vignette * strength:.3f}:mode=forward")
    if lk.sharpen:
        amt = 0.25 + 0.6 * lk.sharpen
        parts.append(f"unsharp=5:5:{amt:.2f}:5:5:0")
    if lk.grain and with_grain:
        g = max(1, round(lk.grain * strength * (height / 1080) ** 0.5))
        parts.append(f"noise=c0s={g}:c0f=t+u")
    return ",".join(parts)
