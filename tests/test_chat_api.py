import json
import uuid

import httpx
import pytest

from app.main import create_app

SITE_URL = "https://site.test/"


async def seed_site(store, provider, with_chunks: bool) -> str:
    site_id = await store.create_site(SITE_URL)
    await store.update_site(site_id, status="ready", contact_url="mailto:hello@site.test")
    if with_chunks:
        page_id = await store.add_page(site_id, SITE_URL + "pricing", "Pricing")
        texts = [
            "Pricing: the starter plan costs 29 dollars per month and includes email support.",
            "Opening hours: the office is open Monday to Friday from nine to five.",
        ]
        emb = await provider.embed(texts)
        await store.add_chunks(
            [
                {
                    "site_id": site_id,
                    "page_id": page_id,
                    "url": SITE_URL + "pricing",
                    "title": "Pricing",
                    "heading": "Plans",
                    "content": t,
                    "token_count": 20,
                    "embedding": v,
                }
                for t, v in zip(texts, emb.vectors)
            ]
        )
    return site_id


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        event, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        events.append((event, json.loads(data)))
    return events


@pytest.fixture
async def client(settings, store, provider):
    app = create_app(settings=settings, store=store, provider=provider)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_no_context_fallback_does_not_generate_an_answer(client, store, provider):
    site_id = await seed_site(store, provider, with_chunks=False)
    body = {"site_id": site_id, "session_id": str(uuid.uuid4()), "message": "What is the capital of France?"}
    r = await client.post("/api/chat", json=body)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names == ["meta", "token", "done"]
    assert "couldn't find that on this website" in events[1][1]["text"]
    assert "hello@site.test" in events[1][1]["text"]
    done = events[2][1]
    assert done["outcome"] == "no_context"
    assert done["model"] is None
    # one small rewrite call and a second embedding, but the answer model never streamed
    assert provider.stream_calls == 0
    assert provider.chat_calls == 1
    assert provider.embed_calls == 2
    assert done["debug"]["rewritten_query"].startswith("Standalone question:")


async def test_no_context_without_rewrite_makes_no_llm_call(settings, store, provider):
    settings.rewrite_on_miss = False
    app = create_app(settings=settings, store=store, provider=provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            site_id = await seed_site(store, provider, with_chunks=False)
            r = await c.post("/api/chat", json={"site_id": site_id, "session_id": str(uuid.uuid4()), "message": "What is the capital of France?"})
    done = parse_sse(r.text)[-1][1]
    assert done["outcome"] == "no_context" and done["tokens_out"] == 0
    assert provider.chat_calls == 0 and provider.embed_calls == 1
    stored = [m for m in store.messages if m["role"] == "assistant"]
    assert stored[-1]["outcome"] == "no_context"


async def test_sse_events_in_order_with_sources(client, store, provider):
    site_id = await seed_site(store, provider, with_chunks=True)
    session = str(uuid.uuid4())
    body = {"site_id": site_id, "session_id": session, "message": "How much does the starter plan cost per month?"}
    r = await client.post("/api/chat", json=body)
    assert r.status_code == 200
    events = parse_sse(r.text)
    names = [e for e, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert set(names[1:-1]) == {"token"}
    meta = events[0][1]
    assert meta["sources"][0]["url"] == SITE_URL + "pricing"
    assert meta["sources"][0]["number"] == 1
    done = events[-1][1]
    assert done["outcome"] == "answered"
    assert done["debug"]["chunks"][0]["similarity"] > 0.3
    assert done["tokens_in"] > 0 and done["cost_usd"] > 0
    assert provider.chat_calls == 1
    answer = "".join(d["text"] for e, d in events if e == "token")
    assert "29 dollars" in answer


async def test_follow_up_is_condensed_with_history(client, store, provider):
    site_id = await seed_site(store, provider, with_chunks=True)
    session = str(uuid.uuid4())
    await client.post("/api/chat", json={"site_id": site_id, "session_id": session, "message": "Tell me about the starter plan"})
    calls_before = provider.chat_calls
    r = await client.post("/api/chat", json={"site_id": site_id, "session_id": session, "message": "what does that cost?"})
    events = parse_sse(r.text)
    done = events[-1][1]
    # one condense call plus one answer call
    assert provider.chat_calls == calls_before + 2
    assert done["debug"]["condensed_query"].startswith("Standalone question:")
    # the answer prompt carried the previous turn
    roles = [m["role"] for m in provider.last_messages]
    assert roles.count("user") >= 2 and "assistant" in roles


async def test_validation_and_unknown_site(client):
    r = await client.post("/api/chat", json={"site_id": "nope", "session_id": str(uuid.uuid4()), "message": "hi"})
    assert r.status_code == 422
    r = await client.post("/api/chat", json={"site_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "message": "x" * 501})
    assert r.status_code == 422
    r = await client.post("/api/chat", json={"site_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "message": "hi"})
    assert r.status_code == 404


async def test_site_not_ready_returns_409(client, store):
    site_id = await store.create_site(SITE_URL)
    r = await client.post("/api/chat", json={"site_id": site_id, "session_id": str(uuid.uuid4()), "message": "hi"})
    assert r.status_code == 409


async def test_chat_rate_limit(settings, store, provider):
    settings.chat_rate_limit = 2
    app = create_app(settings=settings, store=store, provider=provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            site_id = await seed_site(store, provider, with_chunks=False)
            body = {"site_id": site_id, "session_id": str(uuid.uuid4()), "message": "hello"}
            assert (await c.post("/api/chat", json=body)).status_code == 200
            assert (await c.post("/api/chat", json=body)).status_code == 200
            r = await c.post("/api/chat", json=body)
            assert r.status_code == 429
            assert "Retry-After" in r.headers


async def test_daily_cap(settings, store, provider):
    settings.daily_message_cap = 1
    app = create_app(settings=settings, store=store, provider=provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            site_id = await seed_site(store, provider, with_chunks=False)
            body = {"site_id": site_id, "session_id": str(uuid.uuid4()), "message": "hello"}
            assert (await c.post("/api/chat", json=body)).status_code == 200
            r = await c.post("/api/chat", json=body)
            assert r.status_code == 429
            assert "Demo limit reached for today" in r.json()["detail"]


async def test_sites_requires_access_code_and_safe_url(client, monkeypatch):
    r = await client.post("/api/sites", json={"url": "https://example.com", "access_code": "wrong"})
    assert r.status_code == 403
    r = await client.post("/api/sites", json={"url": "http://127.0.0.1:8000", "access_code": "letmein"})
    assert r.status_code == 400
    r = await client.post("/api/sites", json={"url": "http://10.0.0.1", "access_code": "letmein"})
    assert r.status_code == 400
    r = await client.post("/api/sites", json={"url": "ftp://example.com", "access_code": "letmein"})
    assert r.status_code == 400


async def test_sites_create_and_status(client, store, monkeypatch):
    import app.main as main_mod

    async def fake_validate(url):
        return "https://example.com/"

    async def fake_ingest(site_id, url, faq, store_, provider_, settings_):
        await store_.update_site(site_id, status="ready", pages_found=1, pages_done=1, chunk_count=3)

    monkeypatch.setattr(main_mod, "validate_url", fake_validate)
    monkeypatch.setattr(main_mod, "ingest_site", fake_ingest)
    r = await client.post("/api/sites", json={"url": "example.com", "access_code": "letmein", "faq_text": "Q: a? A: b."})
    assert r.status_code == 202
    site_id = r.json()["site_id"]
    import asyncio

    await asyncio.sleep(0)
    s = await client.get(f"/api/sites/{site_id}")
    assert s.status_code == 200
    assert s.json()["status"] in ("queued", "crawling", "ready")
    for _ in range(20):
        if (await client.get(f"/api/sites/{site_id}")).json()["status"] == "ready":
            break
        await asyncio.sleep(0.01)
    assert (await client.get(f"/api/sites/{site_id}")).json()["chunk_count"] == 3


async def test_demo_page_and_widget_served(client, settings):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Proof of concept by Blagojche Josifoski" in r.text
    assert "{{DEFAULT_SITE_ID}}" not in r.text
    assert "–" not in r.text and "—" not in r.text  # no en or em dashes in visible text
    w = await client.get("/widget.js")
    assert w.status_code == 200
    assert "attachShadow" in w.text
