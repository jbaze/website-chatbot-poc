"""LLM and embedding providers behind one small interface.

Only OpenAI is implemented. A Claude provider would implement the same
``LLMProvider`` protocol and be selected with ``LLM_PROVIDER``.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0

    def add(self, other: "Usage") -> None:
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out


@dataclass
class StreamChunk:
    """One item from ``chat_stream``: either a piece of text or the final usage."""

    text: str | None = None
    usage: Usage | None = None


@dataclass
class ChatResult:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    tokens: int = 0


class LLMProvider(Protocol):
    model: str
    embedding_model: str

    def chat_stream(self, messages: list[dict], max_tokens: int) -> AsyncIterator[StreamChunk]: ...

    async def chat(self, messages: list[dict], max_tokens: int) -> ChatResult: ...

    async def embed(self, texts: list[str]) -> EmbedResult: ...


class OpenAIProvider:
    def __init__(self, api_key: str, model: str, embedding_model: str, dimensions: int = 1536) -> None:
        from openai import AsyncOpenAI

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        if not model:
            raise RuntimeError("LLM_MODEL is not set. Set it from the current OpenAI price list.")
        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.embedding_model = embedding_model
        self.dimensions = dimensions

    async def chat_stream(self, messages: list[dict], max_tokens: int) -> AsyncIterator[StreamChunk]:
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_completion_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        async for event in stream:
            if getattr(event, "usage", None) is not None:
                yield StreamChunk(
                    usage=Usage(event.usage.prompt_tokens or 0, event.usage.completion_tokens or 0)
                )
            for choice in event.choices or []:
                delta = choice.delta.content if choice.delta else None
                if delta:
                    yield StreamChunk(text=delta)

    async def chat(self, messages: list[dict], max_tokens: int) -> ChatResult:
        resp = await self._client.chat.completions.create(
            model=self.model, messages=messages, max_completion_tokens=max_tokens
        )
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        usage = (
            Usage(resp.usage.prompt_tokens or 0, resp.usage.completion_tokens or 0)
            if resp.usage
            else Usage()
        )
        return ChatResult(text=text, usage=usage)

    async def embed(self, texts: list[str]) -> EmbedResult:
        if not texts:
            return EmbedResult(vectors=[], tokens=0)
        resp = await self._client.embeddings.create(model=self.embedding_model, input=texts)
        vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        tokens = resp.usage.prompt_tokens if resp.usage else 0
        return EmbedResult(vectors=vectors, tokens=tokens)


_PUNCT = ".,;:!?()[]\"'"


class FakeProvider:
    """Deterministic provider for tests and offline demos. No network calls.

    Embeddings are bag-of-words hashes, so texts sharing words get similar
    vectors. Chat replies quote the first context passage so the UI can be
    exercised end to end without an API key.
    """

    def __init__(
        self, dimensions: int = 1536, model: str = "fake-chat", embedding_model: str = "fake-embed"
    ) -> None:
        self.dimensions = dimensions
        self.model = model
        self.embedding_model = embedding_model
        self.chat_calls = 0
        self.embed_calls = 0
        self.last_messages: list[dict] = []

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for word in text.lower().split():
            word = word.strip(_PUNCT)
            if not word:
                continue
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            vec[h % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> EmbedResult:
        self.embed_calls += 1
        return EmbedResult(
            vectors=[self._embed_one(t) for t in texts],
            tokens=sum(len(t.split()) for t in texts),
        )

    def _reply(self, messages: list[dict]) -> str:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        marker = "[1] "
        if marker in system:
            passage = system.split(marker, 1)[1].split("\n\n", 1)[0]
            return f"Based on the website: {passage[:200].strip()} [1]"
        user = messages[-1]["content"] if messages else ""
        return f"Standalone question: {user}"

    def _usage(self, messages: list[dict], text: str) -> Usage:
        return Usage(
            tokens_in=sum(len(m["content"].split()) for m in messages),
            tokens_out=len(text.split()),
        )

    async def chat(self, messages: list[dict], max_tokens: int) -> ChatResult:
        self.chat_calls += 1
        self.last_messages = messages
        text = self._reply(messages)
        return ChatResult(text=text, usage=self._usage(messages, text))

    async def chat_stream(self, messages: list[dict], max_tokens: int) -> AsyncIterator[StreamChunk]:
        self.chat_calls += 1
        self.last_messages = messages
        text = self._reply(messages)
        words = text.split(" ")
        for i, word in enumerate(words):
            yield StreamChunk(text=word + (" " if i < len(words) - 1 else ""))
        yield StreamChunk(usage=self._usage(messages, text))


def build_provider(settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeProvider(dimensions=settings.embedding_dimensions)
    return OpenAIProvider(
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        embedding_model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
