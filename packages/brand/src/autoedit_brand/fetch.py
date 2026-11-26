"""Safe fetching of user-supplied URLs (PRD 5.10): public addresses only, robots.txt respected, strict
size and time limits, every redirect re-checked."""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "AutocutBot/0.1 (+brand kit intake for the site owner)"
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 12.0
MAX_REDIRECTS = 4


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class Fetched:
    url: str  # final URL after redirects
    content_type: str
    body: bytes


def _allow_private() -> bool:
    return os.environ.get("AUTOEDIT_ALLOW_PRIVATE_FETCH") == "1"  # tests only


def check_url(url: str) -> str:
    """Normalise and validate a URL; refuse anything that resolves to a non-public address (SSRF)."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("bad_scheme", "Only http and https links are supported")
    if not parts.hostname:
        raise FetchError("bad_url", "That link has no host name")
    if parts.username or parts.password:
        raise FetchError("bad_url", "Links with credentials are not allowed")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise FetchError("dns_failed", f"Could not find {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global and not _allow_private():
            raise FetchError("blocked_address", "That address is not on the public internet")
    return url


_robots_cache: dict[str, RobotFileParser | None] = {}


def robots_allows(url: str, client: httpx.Client) -> bool:
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _robots_cache:
        rp: RobotFileParser | None = RobotFileParser()
        try:
            resp = client.get(root + "/robots.txt", timeout=6.0)
            if resp.status_code >= 400:
                rp = None  # no robots.txt: everything allowed
            else:
                assert rp is not None
                rp.parse(resp.text.splitlines())
        except httpx.HTTPError:
            rp = None
        _robots_cache[root] = rp
    rp = _robots_cache[root]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def fetch(url: str, *, max_bytes: int = MAX_HTML_BYTES, accept: str = "text/html,*/*;q=0.5",
          check_robots: bool = True) -> Fetched:  # fmt: skip
    """GET ``url`` with SSRF protection on the URL and on every redirect hop."""
    current = check_url(url)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": accept},
        timeout=TIMEOUT_S,
        follow_redirects=False,
    ) as client:
        if check_robots and not robots_allows(current, client):
            raise FetchError("robots_disallowed", "This site's robots.txt asks automated tools not to read that page")
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    current = check_url(urljoin(current, location))
                    continue
                if resp.status_code >= 400:
                    raise FetchError("http_error", f"The site answered {resp.status_code}")
                chunks, total = [], 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError("too_large", "That page or file is too large to read")
                    chunks.append(chunk)
                return Fetched(url=str(resp.url), content_type=resp.headers.get("content-type", ""),
                               body=b"".join(chunks))  # fmt: skip
        raise FetchError("too_many_redirects", "Too many redirects")
