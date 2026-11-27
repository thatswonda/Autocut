from __future__ import annotations

import io
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from autoedit_brand import fetch as fetch_mod
from autoedit_brand.extract import extract
from autoedit_brand.fetch import FetchError, check_url, fetch
from autoedit_brand.kit import build_kit
from autoedit_brand.palette import brand_colors

PAGE = """<!doctype html><html lang="en"><head><title>Kora Coffee | Fresh roasted</title>
<meta name="description" content="Small-batch coffee roasted in Lagos every morning.">
<meta name="theme-color" content="#e85d04"><link rel="icon" href="/logo.png"></head>
<body><nav><a href="/">Home</a></nav><img class="site-logo" src="/logo.png" alt="Kora logo">
<h1>Fresh coffee, delivered warm</h1><h2>House Blend</h2>
<p>Our House Blend costs ₦4,500 per 250g bag and ships free in Lagos Island.</p>
<ul><li>Roasted the same day</li><li>Recyclable packaging</li></ul>
<blockquote>"Best flat white in Lagos." - Ada</blockquote><a class="btn" href="/shop">Order now</a>
<footer>Secret footer text</footer></body></html>"""


def _png(color: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            body, ctype = b"User-agent: *\nDisallow: /private\n", "text/plain"
        elif self.path == "/logo.png":
            body, ctype = _png((232, 93, 4)), "image/png"
        elif self.path == "/go":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        else:
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("AUTOEDIT_ALLOW_PRIVATE_FETCH", "1")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    fetch_mod._robots_cache.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ssrf_blocked_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOEDIT_ALLOW_PRIVATE_FETCH", raising=False)
    for url in (
        "http://127.0.0.1/",
        "http://localhost:8000/admin",
        "http://169.254.169.254/latest/meta-data",
    ):
        with pytest.raises(FetchError) as exc:
            check_url(url)
        assert exc.value.code == "blocked_address"
    with pytest.raises(FetchError):
        check_url("file:///etc/passwd")


def test_extract_facts_and_brand(site: str) -> None:
    info = extract(fetch(site + "/").body.decode(), site + "/")
    assert info.name == "Kora Coffee"
    assert info.theme_color == "#E85D04"
    assert "₦4,500" in " ".join(info.prices)
    assert "Fresh coffee, delivered warm" in info.headings
    assert "Order now" in info.ctas
    assert "Secret footer text" not in info.as_source_text()
    assert info.as_source_text().startswith("[website:")


def test_robots_and_redirects(site: str) -> None:
    with pytest.raises(FetchError) as exc:
        fetch(site + "/private/page")
    assert exc.value.code == "robots_disallowed"
    assert fetch(site + "/go").url.rstrip("/") == site


def test_build_kit(site: str) -> None:
    kit = build_kit([site + "/"], [])
    assert kit.name == "Kora Coffee"
    assert kit.logo_key is not None
    assert kit.colors["primary"] == "#E85D04"
    assert kit.sites and kit.warnings == []


def test_palette_fallbacks() -> None:
    colors = brand_colors(_png((20, 90, 200)), [], None)
    assert colors["primary"].startswith("#")
    assert set(colors) == {"primary", "secondary", "accent", "background", "text"}


def test_build_kit_reads_every_link_and_key_inner_pages(site: str) -> None:
    second = site.replace("127.0.0.1", "localhost")
    kit = build_kit([site + "/", second + "/"], [])
    hosts = {s.url.split("/")[2] for s in kit.sites}
    assert {site.split("/")[2], second.split("/")[2]} <= hosts  # both links were read
    assert any(s.url.endswith("/shop") for s in kit.sites)  # the "Order now" shop page too
    assert kit.warnings == []
