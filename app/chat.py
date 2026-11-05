"""The answer pipeline: condense, retrieve, fallback or stream. Emits SSE text."""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

from .config import Settings
from .providers import LLMProvider, Usage
from .store import Store

log = logging.getLogger("chat")

SYSTEM_PROMPT = """You are the website assistant for {site_url}. You answer visitors' questions using only the numbered context passages below.

Rules:
- Answer only from the numbered context. Cite the passages you used as [n] right after the sentence they support.
- If the context does not contain the answer, say so plainly and suggest contacting the team. Do not guess.
- Never invent prices, policies, dates, names or contact details.
- Stay on the topic of this website. Politely decline unrelated tasks (coding help, general knowledge, opinions).
- Answer in the same language the visitor writes in.
- Keep answers short: two to five sentences, or a short list. Plain text with optional **bold**, no headings.

Context passages:
{context}"""

CONDENSE_PROMPT = (
    "Rewrite the visitor's latest message as one short standalone question that can be understood "
    "without the conversation. Keep the visitor's language. Resolve words like 'it', 'that' or 'they' "
    "using the conversation. Return only the rewritten question, nothing else."
)


REWRITE_PROMPT = """You rewrite a website visitor's message into one specific, standalone question for searching that website's pages.
Website title: {title}
Website address: {url}
Rules:
- Name the website or company instead of "they", "you", "this page" or "this site".
- Keep the visitor's intent. Do not add facts or guess what the site contains.
- If the website title is not in English, write the question in the title's language.
- If the message is not about this website or its company (for example general knowledge, recipes, weather, sport), return the message exactly as given.
Reply with the question only."""


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def fallback_message(contact_url: str | None) -> str:
    if contact_url:
        contact = contact_url[7:] if contact_url.lower().startswith("mailto:") else contact_url
        return f"I couldn't find that on this website. You can reach the team at {contact}."
    return "I couldn't find that on this website. Please contact the team directly and they will be able to help."


def select_chunks(rows: list[dict], keep: int, per_url: int = 2) -> tuple[list[dict], list[dict]]:
    """Keep the best chunks, at most ``per_url`` per page, and number sources per unique URL.

    Returns (chunks, sources). Each chunk gets a ``number`` matching its source.
    """
    rows = sorted(rows, key=lambda r: r.get("similarity", 0), reverse=True)
    counts: dict[str, int] = {}
    numbers: dict[str, int] = {}
    sources: list[dict] = []
    chosen: list[dict] = []
    for row in rows:
        url = row["url"]
        if counts.get(url, 0) >= per_url:
            continue
        if len(chosen) >= keep:
            break
        counts[url] = counts.get(url, 0) + 1
        if url not in numbers:
            numbers[url] = len(numbers) + 1
            sources.append({"number": numbers[url], "title": row.get("title") or url, "url": url})
        chunk = dict(row)
        chunk["number"] = numbers[url]
        chosen.append(chunk)
    return chosen, sources


def build_context(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        heading = f" / {c['heading']}" if c.get("heading") and c["heading"] != c.get("title") else ""
        parts.append(f"[{c['number']}] {c.get('title') or c['url']}{heading}\n{c['content']}")
    return "\n\n".join(parts)


def estimate_cost(usage: Usage, embed_tokens: int, settings: Settings) -> float:
    return round(
        (
            usage.tokens_in * settings.price_in_per_1m
            + usage.tokens_out * settings.price_out_per_1m
            + embed_tokens * settings.price_embed_per_1m
        )
        / 1_000_000,
        6,
    )


async def stream_chat(
    *,
    store: Store,
    provider: LLMProvider,
    settings: Settings,
    site: dict,
    session_id: str,
    message: str,
    ip_hash: str,
) -> AsyncIterator[str]:
    """Yield SSE frames: meta, token..., done, or error."""
    t_start = time.perf_counter()
    site_id = site["id"]
    usage = Usage()
    embed_tokens = 0
    condensed = message
    rewritten: str | None = None
    outcome = "error"
    answer = ""
    sources: list[dict] = []
    debug_chunks: list[dict] = []
    retrieval_ms = 0
    first_token_ms: int | None = None

    def ms(since: float) -> int:
        return int((time.perf_counter() - since) * 1000)

    try:
        await store.ensure_session(session_id, site_id, ip_hash)
        history = await store.get_history(session_id, settings.history_turns)

        # 1. Condense a follow-up into a standalone question (retrieval only).
        if history:
            convo = "\n".join(f"{m['role']}: {m['content']}" for m in history)
            result = await provider.chat(
                [
                    {"role": "system", "content": CONDENSE_PROMPT},
                    {"role": "user", "content": f"Conversation:\n{convo}\n\nLatest message: {message}"},
                ],
                max_tokens=80,
            )
            usage.add(result.usage)
            if result.text:
                condensed = result.text.strip().strip('"')[: settings.max_message_chars]

        # 2. Retrieve.
        t_ret = time.perf_counter()
        emb = await provider.embed([condensed])
        embed_tokens += emb.tokens
        rows = await store.match_chunks(site_id, emb.vectors[0], settings.match_count, settings.min_similarity)

        # 2b. Second pass for vague or cross-language messages ("what do they do?"):
        # rewrite into a specific question about this site and search once more.
        # This is one small chat call; the answer model is still never called without context.
        if not rows and settings.rewrite_on_miss:
            title = await store.first_page_title(site_id) or site["root_url"]
            result = await provider.chat(
                [
                    {"role": "system", "content": REWRITE_PROMPT.format(title=title, url=site["root_url"])},
                    {"role": "user", "content": condensed},
                ],
                max_tokens=120,
            )
            usage.add(result.usage)
            candidate = result.text.strip().strip('"')[: settings.max_message_chars]
            if candidate and candidate != condensed:
                rewritten = candidate
                emb = await provider.embed([rewritten])
                embed_tokens += emb.tokens
                rows = await store.match_chunks(site_id, emb.vectors[0], settings.match_count, settings.min_similarity)

        chunks, sources = select_chunks(rows, settings.keep_chunks)
        retrieval_ms = ms(t_ret)
        debug_chunks = [
            {
                "number": c["number"],
                "url": c["url"],
                "title": c.get("title"),
                "heading": c.get("heading"),
                "similarity": round(float(c.get("similarity", 0)), 3),
                "preview": (c.get("content") or "")[:240],
            }
            for c in chunks
        ]

        # 3. No context: fixed reply, no LLM call.
        if not chunks:
            outcome = "no_context"
            answer = fallback_message(site.get("contact_url"))
            yield sse("meta", {"session_id": session_id, "sources": []})
            yield sse("token", {"text": answer})
            first_token_ms = ms(t_start)
        else:
            # 4. Stream the grounded answer.
            messages = [{"role": "system", "content": SYSTEM_PROMPT.format(site_url=site["root_url"], context=build_context(chunks))}]
            messages.extend(history[-settings.history_turns :])
            messages.append({"role": "user", "content": message})
            yield sse("meta", {"session_id": session_id, "sources": sources})
            pieces: list[str] = []
            async for chunk in provider.chat_stream(messages, settings.max_answer_tokens):
                if chunk.text:
                    if first_token_ms is None:
                        first_token_ms = ms(t_start)
                    pieces.append(chunk.text)
                    yield sse("token", {"text": chunk.text})
                if chunk.usage:
                    usage.add(chunk.usage)
            answer = "".join(pieces).strip()
            outcome = "answered"

        total_ms = ms(t_start)
        cost = estimate_cost(usage, embed_tokens, settings)
        done = {
            "outcome": outcome,
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "cost_usd": cost,
            "retrieval_ms": retrieval_ms,
            "first_token_ms": first_token_ms,
            "total_ms": total_ms,
            "model": provider.model if outcome == "answered" else None,
            "debug": {"condensed_query": condensed, "rewritten_query": rewritten, "chunks": debug_chunks},
        }
        yield sse("done", done)
    except Exception:  # noqa: BLE001 - friendly error for the visitor, detail in the logs
        log.exception("chat failed", extra={"event": {"site_id": site_id}})
        outcome = "error"
        yield sse("error", {"message": "Something went wrong while answering. Please try again."})
    finally:
        total_ms = ms(t_start)
        cost = estimate_cost(usage, embed_tokens, settings)
        try:
            await store.add_message(
                {"session_id": session_id, "role": "user", "content": message, "outcome": outcome}
            )
            await store.add_message(
                {
                    "session_id": session_id,
                    "role": "assistant",
                    "content": answer,
                    "condensed_query": condensed,
                    "sources": sources,
                    "tokens_in": usage.tokens_in,
                    "tokens_out": usage.tokens_out,
                    "cost_usd": cost,
                    "retrieval_ms": retrieval_ms,
                    "first_token_ms": first_token_ms,
                    "total_ms": total_ms,
                    "outcome": outcome,
                }
            )
        except Exception:  # noqa: BLE001
            log.exception("could not store chat messages")
        log.info(
            "chat",
            extra={
                "event": {
                    "route": "/api/chat",
                    "site_id": site_id,
                    "outcome": outcome,
                    "retrieval_ms": retrieval_ms,
                    "first_token_ms": first_token_ms,
                    "total_ms": total_ms,
                    "tokens_in": usage.tokens_in,
                    "tokens_out": usage.tokens_out,
                    "cost_usd": cost,
                    "ip_hash": ip_hash,
                }
            },
        )
