import os

import pytest

# Tests never touch OpenAI or Supabase.
os.environ.update(
    {
        "LLM_PROVIDER": "fake",
        "STORE": "memory",
        "DEMO_ACCESS_CODE": "letmein",
        "DAILY_MESSAGE_CAP": "1000",
        "ALLOWED_ORIGINS": "http://testserver",
        "PRICE_IN_PER_1M": "0.10",
        "PRICE_OUT_PER_1M": "0.50",
        "PRICE_EMBED_PER_1M": "0.02",
    }
)

from app.config import Settings  # noqa: E402
from app.providers import FakeProvider  # noqa: E402
from app.store import MemoryStore  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(dimensions=64)
