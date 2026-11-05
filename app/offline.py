"""Run the demo fully offline: fake model, in-memory store, one seeded sample site.

    python -m app.offline            # then open http://localhost:8000

Useful for UI work and for seeing the whole flow without an API key or a
database. Answers are canned by the fake provider, but indexing, streaming,
sources, the fallback and the debug panel all behave as in the real thing.
"""
from __future__ import annotations

import asyncio
import os

import uvicorn

from .chunker import Section, chunk_sections
from .config import Settings
from .main import create_app
from .providers import FakeProvider
from .store import MemoryStore

SAMPLE_URL = "https://your-company.example/"
SAMPLE_PAGES = [
    (
        "/",
        "Your Company",
        [
            Section("About us", "Your Company is a small consulting team that helps growing businesses set up and run their software. We started in 2019 and work with clients across Europe and North America, mostly remotely."),
            Section("How we work", "Every engagement starts with a short discovery call. After that we propose a fixed scope and a fixed price, and we send a written weekly update for as long as the project runs."),
        ],
    ),
    (
        "/pricing",
        "Pricing",
        [
            Section("Plans", "The Starter plan costs 290 dollars per month and includes email support within one business day. The Growth plan costs 790 dollars per month and adds a shared Slack channel and a monthly review call. Both plans can be cancelled at the end of any month."),
            Section("Onboarding", "Onboarding is a one time fee of 500 dollars and covers the first two weeks of setup."),
        ],
    ),
    (
        "/contact",
        "Contact",
        [
            Section("Contact", "You can reach the team at hello@your-company.example. Office hours are Monday to Friday, nine to five Central European Time. We usually reply within one business day."),
        ],
    ),
]


async def seed(store: MemoryStore, provider: FakeProvider, settings: Settings) -> str:
    site_id = await store.create_site(SAMPLE_URL)
    total = 0
    for path, title, sections in SAMPLE_PAGES:
        url = SAMPLE_URL.rstrip("/") + path
        page_id = await store.add_page(site_id, url, title)
        chunks = chunk_sections(sections, url, title, settings.chunk_tokens, settings.chunk_overlap_tokens)
        emb = await provider.embed([c.content for c in chunks])
        await store.add_chunks(
            [
                {
                    "site_id": site_id,
                    "page_id": page_id,
                    "url": c.url,
                    "title": c.title,
                    "heading": c.heading,
                    "content": c.content,
                    "token_count": c.token_count,
                    "embedding": v,
                }
                for c, v in zip(chunks, emb.vectors)
            ]
        )
        total += len(chunks)
    await store.update_site(
        site_id,
        status="ready",
        pages_found=len(SAMPLE_PAGES),
        pages_done=len(SAMPLE_PAGES),
        chunk_count=total,
        contact_url="mailto:hello@your-company.example",
    )
    return site_id


def main() -> None:
    for key, value in {
        "LLM_PROVIDER": "fake",
        "STORE": "memory",
        "DEMO_ACCESS_CODE": "demo",
        "ALLOWED_ORIGINS": "http://localhost:8000,http://127.0.0.1:8000",
        "PRICE_IN_PER_1M": "0.10",
        "PRICE_OUT_PER_1M": "0.50",
        "PRICE_EMBED_PER_1M": "0.02",
        # The fake embedding is a bag of words, so plain similarity is lower than a real model's.
        "MIN_SIMILARITY": "0.12",
    }.items():
        os.environ.setdefault(key, value)
    settings = Settings(_env_file=None)
    store = MemoryStore()
    provider = FakeProvider(dimensions=settings.embedding_dimensions)
    settings.default_site_id = asyncio.run(seed(store, provider, settings))
    app = create_app(settings=settings, store=store, provider=provider)
    port = int(os.environ.get("PORT", "8000"))
    print(f"Offline demo on http://localhost:{port}  (access code for 'Try it on your own website': demo)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
