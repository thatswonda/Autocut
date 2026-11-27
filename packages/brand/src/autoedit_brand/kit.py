"""Assemble a brand kit from website links and uploaded files."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .extract import SiteInfo, extract
from .fetch import MAX_IMAGE_BYTES, FetchError, fetch
from .palette import brand_colors, prepare_image

log = logging.getLogger(__name__)


@dataclass
class ImageAsset:
    key: str  # stable id the storyboard refers to
    kind: str  # logo | photo | screenshot
    data: bytes
    ext: str
    width: int
    height: int
    source: str  # where it came from, e.g. website:example.com/ or file:menu.jpg


@dataclass
class BrandKit:
    name: str = ""
    colors: dict[str, str] = field(default_factory=dict)
    logo_key: str | None = None
    tone: str = ""
    language: str | None = None
    sites: list[SiteInfo] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "colors": self.colors,
            "logo_key": self.logo_key,
            "language": self.language,
            "sources": [{"id": s.source_id, "url": s.url, "title": s.title} for s in self.sites],
            "images": [
                {"key": i.key, "kind": i.kind, "source": i.source, "width": i.width, "height": i.height}
                for i in self.images
            ],
            "warnings": self.warnings,
        }


def _try_image(url: str, kind: str, key: str, source: str) -> ImageAsset | None:
    try:
        got = fetch(url, max_bytes=MAX_IMAGE_BYTES, accept="image/*", check_robots=False)
        if "svg" in got.content_type or url.lower().endswith(".svg"):
            return None  # vector logos need rasterising first; skipped in v1
        data, ext, w, h = prepare_image(got.body)
        if min(w, h) < 48:
            return None
        return ImageAsset(key, kind, data, ext, w, h, source)
    except (FetchError, OSError, ValueError) as exc:
        log.info("image %s skipped: %s", url, exc)
        return None


def build_kit(
    urls: list[str], uploads: list[ImageAsset], *, inner_pages: int = 2, photo_total: int = 10
) -> BrandKit:
    """Read every link (and up to ``inner_pages`` key pages of each site: about, products, pricing...),
    share the photo quota fairly between sites, and pick the brand colors."""
    kit = BrandKit()
    kit.images.extend(uploads)
    links = list(dict.fromkeys(u for u in urls if u))[:5]
    per_site = max(2, photo_total // max(1, len(links)))
    for i, url in enumerate(links):
        try:
            page = fetch(url)
        except FetchError as exc:
            kit.warnings.append(f"{url}: {exc}")
            continue
        site = extract(page.body.decode("utf-8", "replace"), page.url)
        kit.sites.append(site)
        if i == 0 or not kit.name:
            kit.name = kit.name or site.name
            kit.language = kit.language or site.language
        for sub_url in site.inner_links[:inner_pages]:
            try:
                sub_page = fetch(sub_url)
            except FetchError:
                continue
            kit.sites.append(extract(sub_page.body.decode("utf-8", "replace"), sub_page.url))
        if not any(im.kind == "logo" for im in kit.images):
            for j, lu in enumerate(site.logo_urls):
                logo = _try_image(lu, "logo", f"web{i}_logo{j}", site.source_id)
                if logo:
                    kit.images.append(logo)
                    break
        taken = 0
        for j, iu in enumerate(site.image_urls[:12]):
            if taken >= per_site or sum(1 for im in kit.images if im.kind == "photo") >= photo_total:
                break
            img = _try_image(iu, "photo", f"web{i}_img{j}", site.source_id)
            if img:
                kit.images.append(img)
                taken += 1
    logo = next((im for im in kit.images if im.kind == "logo"), None)
    kit.logo_key = logo.key if logo else None
    theme = kit.sites[0].theme_color if kit.sites else None
    kit.colors = brand_colors(
        logo.data if logo else None, [im.data for im in kit.images if im.kind != "logo"], theme
    )
    if not logo:
        kit.warnings.append(
            "No logo found: the brand name is shown in type instead. Upload a logo to use it."
        )
    if links and not kit.sites:
        kit.warnings.append("None of the links could be read; the video uses only your prompt and files.")
    return kit
