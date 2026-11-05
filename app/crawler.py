"""Polite, bounded website crawler.

Reads sitemap.xml first when it exists, then crawls breadth first. Stays on
the same site, caps pages and depth, skips binaries, honours robots.txt, waits
between requests, and re-validates every redirect target against the SSRF
rules in ``urlcheck``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .urlcheck import UnsafeURL, validate_url

log = logging.getLogger("crawler")

Validator = Callable[[str], Awaitable[str]]

_SKIP_EXT = re.compile(
    r"\.(pdf|jpe?g|png|gif|webp|svg|ico|bmp|tiff?|mp3|mp4|avi|mov|wmv|webm|zip|gz|tar|rar|7z|"
    r"docx?|xlsx?|pptx?|csv|json|xml|rss|atom|woff2?|ttf|eot|css|js|mjs|map|exe|dmg|apk)(\?|$)",
    re.IGNORECASE,
)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_MAX_REDIRECTS = 5
_MAX_HTML_BYTES = 2_000_000


@dataclass
class FetchedPage:
    url: str
    html: str
    depth: int


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, root_url: str) -> bool:
    a = _strip_www((urlsplit(url).hostname or "").lower())
    b = _strip_www((urlsplit(root_url).hostname or "").lower())
    return bool(a) and a == b


def canonical(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def looks_like_html_url(url: str) -> bool:
    return not _SKIP_EXT.search(urlsplit(url).path)


class Crawler:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_pages: int = 30,
        max_depth: int = 3,
        timeout: float = 10.0,
        delay: float = 0.3,
        user_agent: str = "WebsiteChatbotPOC/0.1",
        validator: Validator | None = None,
    ) -> None:
        self.client = client
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.timeout = timeout
        self.delay = delay
        self.user_agent = user_agent
        self.validate = validator or validate_url
        self.pages_found = 0
        self._robots: RobotFileParser | None = None

    # ---- fetching -------------------------------------------------------

    async def fetch(self, url: str) -> tuple[str, httpx.Response] | None:
        """GET with manual redirect handling. Every hop is SSRF-checked."""
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                current = await self.validate(current)
            except UnsafeURL as exc:
                log.warning("skip unsafe url", extra={"event": {"reason": str(exc)}})
                return None
            try:
                resp = await self.client.get(
                    current,
                    headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
                    timeout=self.timeout,
                    follow_redirects=False,
                )
            except httpx.HTTPError as exc:
                log.warning("fetch failed", extra={"event": {"error": type(exc).__name__}})
                return None
            if resp.is_redirect and resp.headers.get("location"):
                current = urljoin(current, resp.headers["location"])
                continue
            return current, resp
        return None

    async def _fetch_text(self, url: str) -> str | None:
        result = await self.fetch(url)
        if result is None:
            return None
        _, resp = result
        if resp.status_code != 200:
            return None
        return resp.text

    # ---- robots and sitemap ------------------------------------------------

    async def _load_robots(self, root_url: str) -> None:
        parts = urlsplit(root_url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        rp = RobotFileParser()
        text = await self._fetch_text(robots_url)
        if text is None:
            rp.parse([])  # allow everything when robots.txt is missing
        else:
            rp.parse(text.splitlines())
        self._robots = rp

    def allowed(self, url: str) -> bool:
        if self._robots is None:
            return True
        try:
            return self._robots.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def _sitemap_candidates(self, root_url: str) -> list[str]:
        """Sitemaps named in robots.txt first, then the conventional /sitemap.xml."""
        parts = urlsplit(root_url)
        candidates: list[str] = []
        if self._robots is not None:
            try:
                for u in self._robots.site_maps() or []:
                    if same_site(u, root_url):
                        candidates.append(u)
            except Exception:
                pass
        candidates.append(urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", "")))
        return candidates

    async def _sitemap_urls(self, root_url: str) -> list[str]:
        text = None
        for sitemap_url in self._sitemap_candidates(root_url):
            text = await self._fetch_text(sitemap_url)
            if text and "<loc>" in text.lower():
                break
            text = None
        if not text:
            return []
        locs = _LOC_RE.findall(text)
        urls: list[str] = []
        if "<sitemapindex" in text.lower():
            for child in locs[:5]:
                if not same_site(child, root_url):
                    continue
                child_text = await self._fetch_text(child)
                if child_text:
                    urls.extend(_LOC_RE.findall(child_text))
                if len(urls) >= self.max_pages:
                    break
        else:
            urls = locs
        out: list[str] = []
        for u in urls:
            if same_site(u, root_url) and looks_like_html_url(u):
                out.append(canonical(u))
            if len(out) >= self.max_pages:
                break
        return out

    # ---- main loop -------------------------------------------------------

    def _extract_links(self, html: str, base_url: str, root_url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(base_url, href)
            if not absolute.startswith(("http://", "https://")):
                continue
            if same_site(absolute, root_url) and looks_like_html_url(absolute):
                links.append(canonical(absolute))
        return links

    async def crawl(self, root_url: str) -> AsyncIterator[FetchedPage]:
        root_url = canonical(root_url)
        await self._load_robots(root_url)

        frontier: deque[tuple[str, int]] = deque()
        seen: set[str] = set()

        def enqueue(url: str, depth: int) -> None:
            if url in seen or len(seen) >= self.max_pages * 4:
                return
            seen.add(url)
            frontier.append((url, depth))

        enqueue(root_url, 0)
        for u in await self._sitemap_urls(root_url):
            enqueue(u, 0)
        self.pages_found = min(len(frontier), self.max_pages)

        done = 0
        first = True
        while frontier and done < self.max_pages:
            url, depth = frontier.popleft()
            if not self.allowed(url):
                continue
            if not first:
                await asyncio.sleep(self.delay)
            first = False

            result = await self.fetch(url)
            if result is None:
                continue
            final_url, resp = result
            ctype = resp.headers.get("content-type", "")
            if resp.status_code != 200 or not ("text/html" in ctype or "application/xhtml" in ctype or not ctype):
                continue
            if not same_site(final_url, root_url):
                continue
            html = resp.text[:_MAX_HTML_BYTES]
            done += 1
            yield FetchedPage(url=canonical(final_url), html=html, depth=depth)

            if depth < self.max_depth:
                for link in self._extract_links(html, final_url, root_url):
                    enqueue(link, depth + 1)
            self.pages_found = min(done + len(frontier), self.max_pages)
