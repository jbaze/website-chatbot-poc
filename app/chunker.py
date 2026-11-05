"""Split extracted page text into chunks of about N tokens with overlap.

Text arrives as sections (nearest heading plus body). Each section is split
on token windows, so a chunk never crosses a heading boundary and every chunk
keeps its url, page title and nearest heading.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass
class Section:
    heading: str
    text: str


@dataclass
class Chunk:
    url: str
    title: str
    heading: str
    content: str
    token_count: int


@lru_cache
def _encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def _split_tokens(text: str, max_tokens: int, overlap: int) -> list[tuple[str, int]]:
    enc = _encoding()
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return [(text, len(tokens))] if tokens else []
    step = max(1, max_tokens - overlap)
    pieces: list[tuple[str, int]] = []
    start = 0
    while start < len(tokens):
        window = tokens[start : start + max_tokens]
        pieces.append((enc.decode(window).strip(), len(window)))
        if start + max_tokens >= len(tokens):
            break
        start += step
    return pieces


def chunk_sections(
    sections: list[Section],
    url: str,
    title: str,
    max_tokens: int = 500,
    overlap: int = 60,
    min_tokens: int = 20,
) -> list[Chunk]:
    """Chunk a page. Tiny sections are merged into the following one so we do
    not store a chunk that is only a heading."""
    chunks: list[Chunk] = []
    carry = ""
    carry_heading = ""
    for section in sections:
        body = section.text.strip()
        if not body:
            continue
        heading = section.heading.strip() or title
        if carry:
            body = f"{carry}\n\n{body}"
            heading = carry_heading or heading
            carry = ""
        if count_tokens(body) < min_tokens:
            carry, carry_heading = body, heading
            continue
        text = f"{heading}\n{body}" if heading and not body.startswith(heading) else body
        for piece, n in _split_tokens(text, max_tokens, overlap):
            if n < 3:
                continue
            chunks.append(Chunk(url=url, title=title, heading=heading, content=piece, token_count=n))
    if carry and (count_tokens(carry) >= 3):
        chunks.append(
            Chunk(url=url, title=title, heading=carry_heading or title, content=carry, token_count=count_tokens(carry))
        )
    return chunks
