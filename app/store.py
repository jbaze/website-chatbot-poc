"""Persistence behind one small interface.

``SupabaseStore`` talks to Supabase Postgres with pgvector using the service
role key (server side only). ``MemoryStore`` is a drop-in used by the tests
and by ``STORE=memory`` for offline demos.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store(Protocol):
    async def create_site(self, root_url: str) -> str: ...
    async def get_site(self, site_id: str) -> dict | None: ...
    async def update_site(self, site_id: str, **fields: Any) -> None: ...
    async def add_page(self, site_id: str, url: str, title: str) -> str: ...
    async def first_page_title(self, site_id: str) -> str | None: ...
    async def add_chunks(self, chunks: list[dict]) -> None: ...
    async def match_chunks(self, site_id: str, embedding: list[float], count: int, min_similarity: float) -> list[dict]: ...
    async def ensure_session(self, session_id: str, site_id: str, ip_hash: str) -> None: ...
    async def get_history(self, session_id: str, limit: int) -> list[dict]: ...
    async def add_message(self, message: dict) -> None: ...
    async def count_messages_today(self) -> int: ...


# ---------------------------------------------------------------------------


class MemoryStore:
    def __init__(self) -> None:
        self.sites: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}
        self.chunks: list[dict] = []
        self.sessions: dict[str, dict] = {}
        self.messages: list[dict] = []

    async def create_site(self, root_url: str) -> str:
        site_id = str(uuid.uuid4())
        self.sites[site_id] = {
            "id": site_id,
            "root_url": root_url,
            "status": "queued",
            "pages_found": 0,
            "pages_done": 0,
            "chunk_count": 0,
            "error": None,
            "contact_url": None,
            "created_at": _now(),
        }
        return site_id

    async def get_site(self, site_id: str) -> dict | None:
        site = self.sites.get(site_id)
        return dict(site) if site else None

    async def update_site(self, site_id: str, **fields: Any) -> None:
        if site_id in self.sites:
            self.sites[site_id].update(fields)

    async def first_page_title(self, site_id: str) -> str | None:
        for page in self.pages.values():
            if page["site_id"] == site_id:
                return page["title"]
        return None

    async def add_page(self, site_id: str, url: str, title: str) -> str:
        page_id = str(uuid.uuid4())
        self.pages[page_id] = {"id": page_id, "site_id": site_id, "url": url, "title": title, "fetched_at": _now()}
        return page_id

    async def add_chunks(self, chunks: list[dict]) -> None:
        for c in chunks:
            self.chunks.append({"id": str(uuid.uuid4()), **c})

    async def match_chunks(self, site_id: str, embedding: list[float], count: int, min_similarity: float) -> list[dict]:
        qn = math.sqrt(sum(x * x for x in embedding)) or 1.0
        scored: list[dict] = []
        for c in self.chunks:
            if c["site_id"] != site_id:
                continue
            e = c["embedding"]
            dot = sum(a * b for a, b in zip(embedding, e))
            en = math.sqrt(sum(x * x for x in e)) or 1.0
            sim = dot / (qn * en)
            if sim >= min_similarity:
                row = {k: v for k, v in c.items() if k != "embedding"}
                row["similarity"] = sim
                scored.append(row)
        scored.sort(key=lambda r: r["similarity"], reverse=True)
        return scored[:count]

    async def ensure_session(self, session_id: str, site_id: str, ip_hash: str) -> None:
        self.sessions.setdefault(
            session_id, {"id": session_id, "site_id": site_id, "ip_hash": ip_hash, "created_at": _now()}
        )

    async def get_history(self, session_id: str, limit: int) -> list[dict]:
        rows = [m for m in self.messages if m["session_id"] == session_id and m.get("content")]
        return [{"role": m["role"], "content": m["content"]} for m in rows[-limit:]]

    async def add_message(self, message: dict) -> None:
        self.messages.append({"id": str(uuid.uuid4()), "created_at": _now(), **message})

    async def count_messages_today(self) -> int:
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        return sum(1 for m in self.messages if m["role"] == "user" and m["created_at"] >= start)


# ---------------------------------------------------------------------------


class SupabaseStore:
    """Supabase via PostgREST. Vectors are sent as JSON arrays, which pgvector accepts."""

    def __init__(self, url: str, service_role_key: str) -> None:
        self._url = url
        self._key = service_role_key
        self._client = None

    async def client(self):
        if self._client is None:
            from supabase import acreate_client

            self._client = await acreate_client(self._url, self._key)
        return self._client

    async def create_site(self, root_url: str) -> str:
        c = await self.client()
        res = await c.table("sites").insert({"root_url": root_url, "status": "queued"}).execute()
        return res.data[0]["id"]

    async def get_site(self, site_id: str) -> dict | None:
        c = await self.client()
        res = await c.table("sites").select("*").eq("id", site_id).limit(1).execute()
        return res.data[0] if res.data else None

    async def update_site(self, site_id: str, **fields: Any) -> None:
        c = await self.client()
        await c.table("sites").update(fields).eq("id", site_id).execute()

    async def first_page_title(self, site_id: str) -> str | None:
        c = await self.client()
        res = await c.table("pages").select("title").eq("site_id", site_id).order("fetched_at").limit(1).execute()
        return res.data[0]["title"] if res.data else None

    async def add_page(self, site_id: str, url: str, title: str) -> str:
        c = await self.client()
        res = await c.table("pages").insert({"site_id": site_id, "url": url, "title": title[:500]}).execute()
        return res.data[0]["id"]

    async def add_chunks(self, chunks: list[dict]) -> None:
        if not chunks:
            return
        c = await self.client()
        batch = 40
        for i in range(0, len(chunks), batch):
            await c.table("chunks").insert(chunks[i : i + batch]).execute()

    async def match_chunks(self, site_id: str, embedding: list[float], count: int, min_similarity: float) -> list[dict]:
        c = await self.client()
        res = await c.rpc(
            "match_chunks",
            {
                "site_id": site_id,
                "query_embedding": embedding,
                "match_count": count,
                "min_similarity": min_similarity,
            },
        ).execute()
        return list(res.data or [])

    async def ensure_session(self, session_id: str, site_id: str, ip_hash: str) -> None:
        c = await self.client()
        await (
            c.table("chat_sessions")
            .upsert({"id": session_id, "site_id": site_id, "ip_hash": ip_hash}, on_conflict="id", ignore_duplicates=True)
            .execute()
        )

    async def get_history(self, session_id: str, limit: int) -> list[dict]:
        c = await self.client()
        res = (
            await c.table("chat_messages")
            .select("role,content,created_at")
            .eq("session_id", session_id)
            .in_("outcome", ["answered", "no_context"])
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = list(reversed(res.data or []))
        return [{"role": r["role"], "content": r["content"]} for r in rows if r.get("content")]

    async def add_message(self, message: dict) -> None:
        c = await self.client()
        await c.table("chat_messages").insert(message).execute()

    async def count_messages_today(self) -> int:
        c = await self.client()
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        res = (
            await c.table("chat_messages")
            .select("id", count="exact", head=True)
            .eq("role", "user")
            .gte("created_at", start)
            .execute()
        )
        return int(res.count or 0)


def build_store(settings) -> Store:
    if settings.store_kind == "supabase":
        return SupabaseStore(settings.supabase_url, settings.supabase_service_role_key)
    return MemoryStore()
