"""Ingestion: crawl, extract, chunk, embed, store. Also the preload CLI.

    python -m app.ingest --url https://example.com [--faq-file faq.txt]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

import httpx

from .chunker import Section, chunk_sections
from .config import Settings, get_settings
from .crawler import Crawler
from .extract import extract_sections, find_contact_links
from .logging_setup import log_event, setup_logging
from .providers import LLMProvider, build_provider
from .store import Store, build_store

log = logging.getLogger("ingest")

EMBED_BATCH = 32


def _pick_contact(links: list[str]) -> str | None:
    for link in links:
        if link.lower().startswith("mailto:"):
            return link
    return links[0] if links else None


async def _embed_and_store(
    store: Store, provider: LLMProvider, site_id: str, page_id: str, chunks, settings: Settings
) -> int:
    rows: list[dict] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        result = await provider.embed([c.content for c in batch])
        for chunk, vector in zip(batch, result.vectors):
            rows.append(
                {
                    "site_id": site_id,
                    "page_id": page_id,
                    "url": chunk.url,
                    "title": chunk.title[:500],
                    "heading": chunk.heading[:500],
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "embedding": vector,
                }
            )
    await store.add_chunks(rows)
    return len(rows)


async def ingest_site(
    site_id: str,
    root_url: str,
    faq_text: str | None,
    store: Store,
    provider: LLMProvider,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> None:
    started = time.perf_counter()
    await store.update_site(site_id, status="crawling", error=None)
    own_client = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": settings.user_agent})
    pages_done = 0
    chunk_total = 0
    contact_url: str | None = None
    try:
        crawler = Crawler(
            client,
            max_pages=settings.max_pages,
            max_depth=settings.max_depth,
            timeout=settings.page_timeout_seconds,
            delay=settings.crawl_delay_seconds,
            user_agent=settings.user_agent,
        )
        async for page in crawler.crawl(root_url):
            title, sections = extract_sections(page.html, page.url)
            if contact_url is None:
                contact_url = _pick_contact(find_contact_links(page.html, page.url))
            chunks = chunk_sections(
                sections, page.url, title or page.url, settings.chunk_tokens, settings.chunk_overlap_tokens
            )
            page_id = await store.add_page(site_id, page.url, title or page.url)
            if chunks:
                chunk_total += await _embed_and_store(store, provider, site_id, page_id, chunks, settings)
            pages_done += 1
            await store.update_site(
                site_id,
                pages_found=max(crawler.pages_found, pages_done),
                pages_done=pages_done,
                chunk_count=chunk_total,
            )
            log_event(log, "page indexed", site_id=site_id, url=page.url, chunks=len(chunks))

        if faq_text and faq_text.strip():
            faq_url = root_url.rstrip("/") + "/#faq"
            sections = [Section(heading="FAQ", text=faq_text.strip())]
            chunks = chunk_sections(sections, faq_url, "FAQ", settings.chunk_tokens, settings.chunk_overlap_tokens)
            page_id = await store.add_page(site_id, faq_url, "FAQ")
            chunk_total += await _embed_and_store(store, provider, site_id, page_id, chunks, settings)
            pages_done += 1

        if chunk_total == 0:
            await store.update_site(
                site_id,
                status="failed",
                pages_done=pages_done,
                error="No readable text was found on that website.",
            )
            return
        await store.update_site(
            site_id,
            status="ready",
            pages_found=pages_done,
            pages_done=pages_done,
            chunk_count=chunk_total,
            contact_url=contact_url,
        )
        log_event(
            log,
            "site ready",
            site_id=site_id,
            pages=pages_done,
            chunks=chunk_total,
            total_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - we want the site row to say failed
        log.exception("ingest failed", extra={"event": {"site_id": site_id}})
        await store.update_site(site_id, status="failed", error=f"Indexing failed ({type(exc).__name__}).")
    finally:
        if own_client:
            await client.aclose()


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Preload a website into the chatbot index.")
    parser.add_argument("--url", required=True, help="Root URL of a site you own or may index.")
    parser.add_argument("--faq-file", help="Optional text file with FAQ content.")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    store = build_store(settings)
    provider = build_provider(settings)

    from .urlcheck import validate_url

    url = await validate_url(args.url)
    faq = open(args.faq_file, encoding="utf-8").read() if args.faq_file else None
    site_id = await store.create_site(url)
    print(f"site_id: {site_id}")
    await ingest_site(site_id, url, faq, store, provider, settings)
    site = await store.get_site(site_id)
    print(f"status: {site['status']}  pages: {site['pages_done']}  chunks: {site['chunk_count']}")
    if site["status"] == "ready":
        print(f"Set DEFAULT_SITE_ID={site_id} so the demo page uses this site.")
        return 0
    print(f"error: {site.get('error')}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
