# Website AI chatbot: proof of concept

A clickable proof of concept of an AI chat widget that answers visitors' questions from a website's own pages, with source links. Built on the stack from the job post: **Python 3.12, FastAPI, Supabase (Postgres + pgvector) and the OpenAI API**, with basic retrieval augmented generation.

This is evidence that the approach works, not production software. See "What changes for production" below.

## What it does

- A chat bubble on any page, embedded with one `<script>` tag. Answers stream in with numbered source links.
- Follow-up questions ("what does that cost?") are rewritten into standalone questions before retrieval, so they resolve correctly.
- If nothing on the site matches, the bot says so and points to the contact page. The language model is not called at all in that case.
- "Try it on your own website": paste a URL (plus an access code), up to 30 pages are crawled and indexed with a live progress line, then the chat answers about that site.
- A "How this answer was made" panel shows the condensed question, retrieved passages with similarity scores, timings, tokens and estimated cost.

## Architecture

```
Visitor's browser
  └─ widget.js (Shadow DOM)  ──SSE──>  FastAPI
                                          ├─ /api/chat     condense → embed → match_chunks → (rewrite and retry on miss) → stream answer
                                          ├─ /api/sites    validate URL → background crawl → chunk → embed → store
                                          └─ static: /  (demo page), /widget.js
                                       Supabase Postgres + pgvector
                                          sites, pages, chunks, chat_sessions, chat_messages
                                       OpenAI API (server side only)
```

Code map:

| Path | What it does |
|---|---|
| `app/main.py` | FastAPI app, routes, CORS, request id middleware, rate limits |
| `app/chat.py` | Condense, retrieve, second-pass rewrite on a miss, no-context fallback, streaming answer, SSE framing |
| `app/ingest.py` | Crawl, extract, chunk, embed, store. Also the preload CLI |
| `app/crawler.py` | Sitemap first, then breadth first. Same site, 30 pages, depth 3, robots.txt, redirect checks |
| `app/extract.py` | trafilatura with a BeautifulSoup fallback, split into heading sections |
| `app/chunker.py` | About 500 token chunks with 60 token overlap (tiktoken) |
| `app/urlcheck.py` | SSRF protection for submitted URLs |
| `app/providers.py` | `LLMProvider` interface. OpenAI implemented, plus a fake for tests |
| `app/store.py` | `Store` interface. Supabase implemented, plus in-memory for tests |
| `static/widget.js` | The embeddable widget |
| `static/index.html` | The demo landing page |
| `supabase/migrations/001_init.sql` | Schema, `match_chunks` function, RLS |

## Run locally

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; on macOS or Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in the keys
uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000. Preload a site you own:

```bash
python -m app.ingest --url https://blagojche.dev
```

Copy the printed `site_id` into `DEFAULT_SITE_ID` in `.env` and restart.

**Offline mode** (no API key, no database) for UI work: set `LLM_PROVIDER=fake` and `STORE=memory`. Answers are canned but the whole flow works, including indexing, streaming, sources and the debug panel.

Tests mock every LLM, embedding and network call:

```bash
pytest
```

## Supabase setup

1. Create a free project.
2. In the SQL editor, run `supabase/migrations/001_init.sql`. It enables `vector`, creates the tables, the `match_chunks` function and the HNSW index, and turns on row level security with no policies for `anon`. Only the backend, with the service role key, reads or writes.
3. Copy the project URL and the service role key into the environment.

## Environment variables

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Dedicated project key. Set a hard monthly budget in the OpenAI dashboard. |
| `LLM_MODEL` | Chat model id. Set from the current price list at deploy, not hardcoded. |
| `EMBEDDING_MODEL` | Default `text-embedding-3-small` (1536 dimensions). |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Server side only. Never sent to the browser. |
| `ALLOWED_ORIGINS` | Comma separated CORS origins: the demo origin, plus the client's site later. |
| `DEMO_ACCESS_CODE` | Required to index a new site. Shared privately in the proposal. |
| `DEFAULT_SITE_ID` | The preloaded site shown on the demo page. |
| `DAILY_MESSAGE_CAP` | Global cap on user messages per day, counted in `chat_messages`. |
| `PRICE_IN_PER_1M`, `PRICE_OUT_PER_1M`, `PRICE_EMBED_PER_1M` | USD per 1M tokens for cost estimates, from the current price list. |
| `MIN_SIMILARITY` | Retrieval threshold. 0.25 was tuned on blagojche.dev with text-embedding-3-small: off-topic questions scored at most 0.21, on-topic ones at least 0.28. |
| `REWRITE_ON_MISS` | Default true. When nothing clears the threshold, one small chat call rewrites the message into a specific question about the site (in the site's language) and retrieval runs once more. Vague openers like "what do they do?" score about 0.19 on their own and 0.5 to 0.7 after the rewrite. Off-topic messages come back unchanged and still get the fallback. Set to false for the strict mode where a miss makes no chat call at all. |
| `LLM_REASONING_EFFORT` | Default `none`. gpt-6-luna is a reasoning model and otherwise spends the output budget on hidden reasoning before the first visible token. Set empty for models that reject the parameter. |
| `LLM_PROVIDER`, `STORE` | `openai`/`fake` and `supabase`/`memory`. Defaults pick the real ones. |

## Embed on any website

```html
<script src="https://YOUR-HOST/widget.js" data-api="https://YOUR-HOST"
        data-site="SITE_ID" data-title="Ask us" data-color="#2563eb" defer></script>
```

Add the website's origin to `ALLOWED_ORIGINS`. The widget uses Shadow DOM, so the host page's CSS cannot break it and vice versa. Add `?debug=1` to the page URL to open the "How this answer was made" panel by default.

## API

| Route | Notes |
|---|---|
| `GET /api/health` | Store, provider and model in use. |
| `POST /api/chat` | `{ site_id, session_id, message }`. Responds with Server-Sent Events: `meta` (sources), `token` events, then `done` (tokens, cost, timings, outcome, debug), or `error`. Messages are capped at 500 characters. |
| `POST /api/sites` | `{ url, faq_text?, access_code }`. Validates, queues a background crawl, returns `{ site_id }`. |
| `GET /api/sites/{id}` | `status`, `pages_found`, `pages_done`, `chunk_count`, `error`. Polled every two seconds by the demo page. |

## Cost and abuse protection

- Indexing requires the access code. Submitted URLs must be http or https and must not resolve to loopback, private, link-local or cloud metadata ranges. Every redirect is checked again.
- Per IP: 20 chat messages per 10 minutes, 2 indexing jobs per hour (in memory, single instance).
- A daily message cap across the whole demo.
- The answer model never runs when retrieval finds nothing. With `REWRITE_ON_MISS` on, a miss costs one small rewrite call (about 150 tokens in, 20 out) before the fallback. `max_tokens` is 400 for answers, 80 for the condense step and 120 for the rewrite.
- API keys live only on the server. IPs are hashed before logging or storage.

**Estimated cost per 1,000 messages.** Measured on 2026-11-05 with gpt-6-luna and text-embedding-3-small on two indexed sites (blagojche.dev and a small Macedonian site): eleven grounded answers, including follow-ups and rewritten vague questions, averaged about 980 input tokens, 60 output tokens and $0.00013 each, so about **$0.13 per 1,000 answered messages** at the prices in `.env.example`. No-context fallbacks cost about $0.00002 each (embedding plus the rewrite call). Time to first token in that run was 1.2 to 2.5 seconds for direct questions and 3 to 4 seconds when the condense or rewrite call ran first. Each `done` event and each `chat_messages` row carries `tokens_in`, `tokens_out` and `cost_usd`, so the figure can be re-derived from real traffic.

## Known limits

- One web service instance: rate limits are in memory.
- Crawl is capped at 30 pages and depth 3. JavaScript rendered sites are read as served HTML.
- "Same site" means the same host name, ignoring `www`. Subdomains are not followed.
- The condense step and answer share the last six messages only.
- The fallback message is in English regardless of the visitor's language.
- `pages_found` is an estimate while crawling (sitemap size or frontier size, capped at 30).

## What changes for production

- An admin view of conversations, with search and export.
- A scheduled re-crawl so answers follow the website.
- An evaluation set of real visitor questions with expected sources, run on every prompt or model change.
- Redis-backed rate limits and the daily cap, so more than one instance can run.
- Monitoring and alerts: error rate, time to first token, daily spend.
- The client's own OpenAI and Supabase accounts, with the keys rotated.

## Deploy (Render)

`render.yaml` describes one web service with the health check on `/api/health`. Set the secret env vars in the Render dashboard, run the Supabase migration, deploy, then run the preload CLI from a shell on the service (or locally against the same Supabase project) and set `DEFAULT_SITE_ID`.

---

Proof of concept by Blagojche Josifoski. No real company is represented in the demo.
