import httpx

from app.ingest import ingest_site
from app.urlcheck import UnsafeURL

HTML = """
<html><head><title>Acme Tools</title></head><body>
<nav><a href="/contact">Contact</a></nav>
<main>
<h1>Acme Tools</h1>
<p>We build small, sharp tools for carpenters. Our workshop has been running since 1998 and we ship worldwide.</p>
<h2>Pricing</h2>
<p>The basic set costs 120 euros. The professional set costs 340 euros and includes a lifetime sharpening service.</p>
</main></body></html>
"""


async def allow_all(url: str) -> str:
    return url


async def test_ingest_end_to_end_with_faq(store, provider, settings, monkeypatch):
    import app.crawler as crawler_mod

    monkeypatch.setattr(crawler_mod, "validate_url", allow_all)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/", "/contact"):
            return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings.crawl_delay_seconds = 0
    site_id = await store.create_site("https://acme.test/")
    await ingest_site(site_id, "https://acme.test/", "Q: Do you ship to Japan? A: Yes, within ten days.", store, provider, settings, client=client)

    site = await store.get_site(site_id)
    assert site["status"] == "ready"
    assert site["pages_done"] == 3  # root, contact, synthetic FAQ
    assert site["chunk_count"] >= 3
    assert site["contact_url"] == "https://acme.test/contact"
    faq = [c for c in store.chunks if c["title"] == "FAQ"]
    assert faq and "Japan" in faq[0]["content"]
    assert all(c["url"].startswith("https://acme.test") for c in store.chunks)
    assert all(len(c["embedding"]) == provider.dimensions for c in store.chunks)


async def test_ingest_marks_failed_when_nothing_readable(store, provider, settings, monkeypatch):
    import app.crawler as crawler_mod

    monkeypatch.setattr(crawler_mod, "validate_url", allow_all)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    settings.crawl_delay_seconds = 0
    site_id = await store.create_site("https://down.test/")
    await ingest_site(site_id, "https://down.test/", None, store, provider, settings, client=client)
    site = await store.get_site(site_id)
    assert site["status"] == "failed"
    assert site["error"]
