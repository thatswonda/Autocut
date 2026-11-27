"""Brand colors from images (logos, product shots) and image preparation."""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

try:  # iPhone photos (HEIC/HEIF)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - optional on dev machines
    pass


@dataclass
class Swatch:
    hex: str
    share: float
    saturation: float
    lightness: float


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def swatches(data: bytes, k: int = 6) -> list[Swatch]:
    """Dominant colors, ignoring transparent pixels."""
    im: Image.Image = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("RGBA")
    im.thumbnail((160, 160))
    raw = im.tobytes()
    px = [tuple(raw[i : i + 3]) for i in range(0, len(raw), 4) if raw[i + 3] > 200]
    if not px:
        return []
    flat = Image.new("RGB", (len(px), 1))
    flat.putdata(px)
    q = flat.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    out = []
    for count, idx in counts:
        base = int(idx) * 3  # type: ignore[call-overload]
        r, g, b = pal[base : base + 3]
        h, light, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        del h
        out.append(Swatch(_hex((r, g, b)), int(count) / len(px), s, light))
    return out


def brand_colors(logo: bytes | None, images: list[bytes], theme_color: str | None) -> dict[str, str]:
    """Pick primary / secondary / accent / background / text, with sensible fallbacks."""
    cands: list[Swatch] = []
    for data in [d for d in [logo] if d] + images[:3]:
        try:
            weight = 2.0 if data is logo else 1.0
            for sw in swatches(data):
                cands.append(Swatch(sw.hex, sw.share * weight, sw.saturation, sw.lightness))
        except Exception:
            continue
    vivid = sorted(
        [c for c in cands if c.saturation > 0.35 and 0.18 < c.lightness < 0.82],
        key=lambda c: c.share * (0.5 + c.saturation),
        reverse=True,
    )
    darks = sorted([c for c in cands if c.lightness < 0.25], key=lambda c: -c.share)
    primary = theme_color or (vivid[0].hex if vivid else "#1F2937")
    accent = next((c.hex for c in vivid if c.hex != primary and _distance(c.hex, primary) > 80), None)
    secondary = darks[0].hex if darks else "#1E1E24"
    return {
        "primary": primary.upper(),
        "secondary": secondary.upper(),
        "accent": (accent or "#FFB703").upper(),
        "background": "#FAFAF7",
        "text": "#141414",
    }


def _distance(a: str, b: str) -> float:
    pa = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return sum((x - y) ** 2 for x, y in zip(pa, pb, strict=True)) ** 0.5


def prepare_image(data: bytes, max_edge: int = 1600) -> tuple[bytes, str, int, int]:
    """Normalise an uploaded or fetched image: orientation fixed, bounded size. Keeps transparency."""
    im: Image.Image = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im) or im
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    im.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    if has_alpha:
        im.save(buf, "PNG", optimize=True)
        ext = ".png"
    else:
        im.save(buf, "JPEG", quality=90)
        ext = ".jpg"
    return buf.getvalue(), ext, im.width, im.height
