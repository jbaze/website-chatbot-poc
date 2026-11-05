import httpx
import pytest

from app.crawler import Crawler
from app.urlcheck import UnsafeURL

ROOT = "https://site.test"


def page(n: int, total: int) -> str:
    links = "".join(f'<a href="/p/{i}">page {i}</a>' for i in range(total))
    links += '<a href="https://other.test/x">offsite</a><a href="/file.pdf">pdf</a>'
    return f"<html><head><title>Page {n}</title></head><body><h1>Page {n}</h1><p>Body {n}</p>{links}</body></html>"


def make_site(total: int, robots: str | None = None, sitemap: str | None = None, redirect_to: str | None = None):
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap, headers={"content-type": "application/xml"}) if sitemap else httpx.Response(404)
        if path == "/go" and redirect_to:
            return httpx.Response(302, headers={"location": redirect_to})
        if path == "/file.pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        if path == "/" or path.startswith("/p/"):
            n = 0 if path == "/" else int(path.split("/")[-1])
            return httpx.Response(200, text=page(n, total), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), hits


async def allow_all(url: str) -> str:
    if "private.test" in url:
        raise UnsafeURL("private")
    return url


async def test_crawler_stops_at_30_pages_and_stays_on_domain():
    client, hits = make_site(total=60)
    crawler = Crawler(client, max_pages=30, delay=0, validator=allow_all)
    pages = [p async for p in crawler.crawl(ROOT)]

    assert len(pages) == 30
    assert all(p.url.startswith(ROOT) for p in pages)
    assert not any("other.test" in h for h in hits)
    assert not any(h.endswith(".pdf") for h in hits)


async def test_crawler_honours_robots():
    client, hits = make_site(total=5, robots="User-agent: *\nDisallow: /p/3\n")
    crawler = Crawler(client, max_pages=30, delay=0, validator=allow_all)
    pages = [p async for p in crawler.crawl(ROOT)]
    urls = {p.url for p in pages}
    assert f"{ROOT}/p/3" not in urls
    assert f"{ROOT}/p/2" in urls


async def test_crawler_reads_sitemap_first():
    sitemap = f"<urlset><url><loc>{ROOT}/p/4</loc></url><url><loc>https://other.test/a</loc></url></urlset>"
    client, hits = make_site(total=0, sitemap=sitemap)
    crawler = Crawler(client, max_pages=30, delay=0, validator=allow_all)
    pages = [p async for p in crawler.crawl(ROOT)]
    assert {p.url for p in pages} == {ROOT + "/", f"{ROOT}/p/4"}
    assert crawler.pages_found >= 2


async def test_redirect_into_private_range_is_dropped():
    client, hits = make_site(total=0, redirect_to="http://private.test/admin")
    crawler = Crawler(client, max_pages=30, delay=0, validator=allow_all)
    result = await crawler.fetch(f"{ROOT}/go")
    assert result is None
    assert not any("private.test" in h for h in hits)
