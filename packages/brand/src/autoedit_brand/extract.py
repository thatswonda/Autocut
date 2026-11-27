"""Turn a web page into brand signals and source-tagged facts.

Facts keep their source (``website:/path``) so every claim in a storyboard can be traced back to where
it came from (PRD fact grounding rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

PRICE_RE = re.compile(
    r"(?:[$€£₦¥₹]|NGN|USD|EUR|GBP|KES|GHS|ZAR)\s?\d[\d,]*(?:\.\d{1,2})?(?:\s?(?:/|per)\s?\w+)?",
    re.IGNORECASE,
)
HEX_RE = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$", re.IGNORECASE)
MAX_TEXT_CHARS = 9000


@dataclass
class SiteInfo:
    url: str
    source_id: str
    name: str = ""
    title: str = ""
    description: str = ""
    theme_color: str | None = None
    logo_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    list_items: list[str] = field(default_factory=list)
    ctas: list[str] = field(default_factory=list)
    prices: list[str] = field(default_factory=list)
    language: str | None = None
    inner_links: list[str] = field(default_factory=list)  # same-site pages worth reading (about, products...)

    def as_source_text(self) -> str:
        """Compact, source-labelled text for the storyboard prompt (bounded size)."""
        parts = [f"[{self.source_id}] {self.url}"]
        for label, value in (
            ("Site name", self.name),
            ("Title", self.title),
            ("Description", self.description),
        ):
            if value:
                parts.append(f"{label}: {value}")
        if self.headings:
            parts.append("Headings: " + " | ".join(self.headings[:25]))
        if self.list_items:
            parts.append("List items: " + " | ".join(self.list_items[:40]))
        if self.prices:
            parts.append("Prices seen: " + ", ".join(dict.fromkeys(self.prices[:15])))
        if self.ctas:
            parts.append("Buttons/CTAs: " + " | ".join(dict.fromkeys(self.ctas[:12])))
        if self.paragraphs:
            parts.append("Body text:\n" + "\n".join(self.paragraphs))
        text = "\n".join(parts)
        return text[:MAX_TEXT_CHARS]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _norm_color(c: str | None) -> str | None:
    if not c:
        return None
    c = c.strip()
    if HEX_RE.match(c):
        if len(c) == 4:
            c = "#" + "".join(ch * 2 for ch in c[1:])
        return c.upper()
    return None


def _src(tag: Tag, base: str, *attrs: str) -> str | None:
    for a in attrs:
        v = tag.get(a)
        if isinstance(v, str) and v.strip() and not v.startswith("data:"):
            return urljoin(base, v.strip().split(" ")[0])
    return None


def extract(html: str, url: str) -> SiteInfo:
    soup = BeautifulSoup(html, "html.parser")
    path = urlsplit(url).path or "/"
    info = SiteInfo(url=url, source_id=f"website:{urlsplit(url).netloc}{path}")

    html_tag = soup.find("html")
    if isinstance(html_tag, Tag) and isinstance(html_tag.get("lang"), str):
        info.language = str(html_tag.get("lang")).split("-")[0].lower() or None

    def meta(*names: str) -> str:
        for n in names:
            m = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if isinstance(m, Tag) and isinstance(m.get("content"), str):
                return _clean(str(m.get("content")))
        return ""

    info.title = _clean(soup.title.get_text()) if soup.title else ""
    info.name = meta("og:site_name", "application-name") or info.title.split("|")[0].split(" - ")[0].strip()
    info.description = meta("og:description", "description", "twitter:description")
    info.theme_color = _norm_color(meta("theme-color", "msapplication-TileColor"))

    # Logo candidates: explicit logo images first, then touch icons, then favicons.
    for img in soup.find_all("img"):
        hay = " ".join(str(img.get(a, "")) for a in ("class", "id", "alt", "src")).lower()
        if "logo" in hay:
            src = _src(img, url, "src", "data-src")
            if src:
                info.logo_urls.append(src)
    for rel in ("apple-touch-icon", "icon", "shortcut icon"):
        for link in soup.find_all("link"):
            rels = " ".join(link.get("rel") or []).lower()
            if rels == rel:
                src = _src(link, url, "href")
                if src:
                    info.logo_urls.append(src)
    og = meta("og:image", "twitter:image")
    if og:
        info.image_urls.append(urljoin(url, og))
    for img in soup.find_all("img"):
        src = _src(img, url, "src", "data-src")
        if not src or src in info.logo_urls:
            continue
        try:
            w = int(str(img.get("width") or 0))
        except ValueError:
            w = 0
        if w and w < 200:
            continue
        info.image_urls.append(src)
    info.image_urls = list(dict.fromkeys(info.image_urls))[:12]
    info.logo_urls = list(dict.fromkeys(info.logo_urls))[:5]

    host = urlsplit(url).netloc.lower().removeprefix("www.")
    keys = ("about", "product", "shop", "store", "service", "pricing", "price", "plan", "menu", "feature",
            "catalog", "collection", "contact", "how-it-works", "faq")  # fmt: skip
    for a in soup.find_all("a", href=True):
        href = urljoin(url, str(a.get("href")))
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or parts.netloc.lower().removeprefix("www.") != host:
            continue
        hay = (parts.path + " " + a.get_text(" ")).lower()
        clean = f"{parts.scheme}://{parts.netloc}{parts.path}"
        if any(k in hay for k in keys) and clean.rstrip("/") != url.split("?")[0].rstrip("/"):
            info.inner_links.append(clean)
    info.inner_links = list(dict.fromkeys(info.inner_links))[:6]

    for t in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        t.decompose()
    for t in soup.find_all(["nav", "footer"]):
        # keep navigation and footers out of the copy but harvest CTAs/prices below first
        t.decompose()
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = _clean(h.get_text(" "))
        if 2 <= len(text) <= 140:
            info.headings.append(text)
    for li in soup.find_all("li"):
        text = _clean(li.get_text(" "))
        if 3 <= len(text) <= 160:
            info.list_items.append(text)
    for a in soup.find_all(["a", "button"]):
        text = _clean(a.get_text(" "))
        if 2 <= len(text) <= 28 and len(text.split()) <= 4:
            info.ctas.append(text)
    total = 0
    for p in soup.find_all(["p", "blockquote", "td"]):
        text = _clean(p.get_text(" "))
        if len(text) < 25:
            continue
        info.paragraphs.append(text)
        total += len(text)
        if total > 6000:
            break
    body_text = _clean(soup.get_text(" "))
    info.prices = PRICE_RE.findall(body_text)[:20]
    info.headings = list(dict.fromkeys(info.headings))
    info.list_items = list(dict.fromkeys(info.list_items))
    return info
