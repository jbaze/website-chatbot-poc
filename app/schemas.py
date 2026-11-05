"""Request and response bodies. Everything the browser sends is validated here."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    site_id: UUID
    session_id: UUID
    message: str = Field(min_length=1, max_length=500)

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Message is empty.")
        return v


class SiteCreateRequest(BaseModel):
    url: str = Field(min_length=3, max_length=2000)
    faq_text: str | None = Field(default=None, max_length=20000)
    access_code: str = Field(min_length=1, max_length=200)


class SiteCreateResponse(BaseModel):
    site_id: str


class SiteStatusResponse(BaseModel):
    site_id: str
    root_url: str
    status: str
    pages_found: int = 0
    pages_done: int = 0
    chunk_count: int = 0
    error: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    store: str
    provider: str
    model: str
