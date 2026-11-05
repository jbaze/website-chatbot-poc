"""Main-text extraction: trafilatura first, BeautifulSoup as the fallback.

Both paths produce a page title and a list of ``Section`` objects, where each
section is the nearest heading plus the text under it.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .chunker import Section

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


def _sections_from_markdown(md: str) -> list[Section]:
    sections: list[Section] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append(Section(heading=heading, text=text))
        buf.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        m = _HEADING_RE.match(line)
        if m:
            flush()
            heading = _MD_LINK_RE.sub(r"\1", m.group(2)).strip("# ").strip()
            continue
        line = _MD_LINK_RE.sub(r"\1", line)
        buf.append(line)
    flush()
    return sections


def _extract_with_trafilatura(html: str, url: str) -> list[Section] | None:
    try:
        import trafilatura
    except Exception:  # pragma: no cover - optional at import time
        return None
    try:
        md = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=False,
            include_tables=True,
            include_comments=False,
            favor_recall=True,
        )
    except Exception:
        return None
    if not md or len(md.strip()) < 40:
        return None
    return _sections_from_markdown(md)


_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"}
_BLOCK_TAGS = {"p", "li", "td", "th", "blockquote", "pre", "dd", "dt", "figcaption"}


def _extract_with_bs4(html: str) -> list[Section]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(list(_SKIP_TAGS)):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    sections: list[Section] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append(Section(heading=heading, text=text))
        buf.clear()

    for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", *_BLOCK_TAGS]):
        if el.name.startswith("h"):
            flush()
            heading = el.get_text(" ", strip=True)
            continue
        if el.find_parent(list(_BLOCK_TAGS)) is not None:
            continue  # nested block already covered by its parent
        text = el.get_text(" ", strip=True)
        if text:
            buf.append(text)
    flush()
    if not sections:
        text = root.get_text(" ", strip=True)
        if text:
            sections.append(Section(heading="", text=text))
    return sections


def extract_sections(html: str, url: str) -> tuple[str, list[Section]]:
    """Return (title, sections) for a page."""
    title = _title_from_html(html)
    sections = _extract_with_trafilatura(html, url)
    if not sections:
        sections = _extract_with_bs4(html)
    return title, sections


def find_contact_links(html: str, base_url: str) -> list[str]:
    """Links that look like a contact page or a mailto address."""
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True).lower()
        if href.lower().startswith("mailto:"):
            found.append(href)
        elif "contact" in href.lower() or "contact" in text:
            found.append(urljoin(base_url, href))
    return found
