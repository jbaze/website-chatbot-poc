"""Application settings, loaded from environment variables (and a local .env file)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM / embeddings
    openai_api_key: str = ""
    llm_model: str = Field(default="", description="Set from the current OpenAI price list at deploy.")
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    llm_provider: str = Field(default="openai", description="openai, or fake for tests and offline demos")

    # Database
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    store: str = Field(default="auto", description="supabase, memory or auto")

    # Demo page and widget
    allowed_origins: str = "http://localhost:8000"
    demo_access_code: str = ""
    default_site_id: str = ""

    # Cost and abuse protection
    daily_message_cap: int = 300
    chat_rate_limit: int = 20
    chat_rate_window_seconds: int = 600
    ingest_rate_limit: int = 2
    ingest_rate_window_seconds: int = 3600
    price_in_per_1m: float = 0.0
    price_out_per_1m: float = 0.0
    price_embed_per_1m: float = 0.0

    # Retrieval and answering
    min_similarity: float = 0.25
    match_count: int = 6
    keep_chunks: int = 5
    max_answer_tokens: int = 400
    history_turns: int = 6
    max_message_chars: int = 500

    # Ingestion
    max_pages: int = 30
    max_depth: int = 3
    page_timeout_seconds: float = 10.0
    crawl_delay_seconds: float = 0.3
    chunk_tokens: int = 500
    chunk_overlap_tokens: int = 60
    user_agent: str = "WebsiteChatbotPOC/0.1 (proof of concept crawler; https://blagojche.dev)"

    log_level: str = "INFO"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def store_kind(self) -> str:
        if self.store != "auto":
            return self.store
        return "supabase" if self.supabase_url and self.supabase_service_role_key else "memory"


@lru_cache
def get_settings() -> Settings:
    return Settings()
