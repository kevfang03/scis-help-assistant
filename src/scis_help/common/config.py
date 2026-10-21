"""Typed settings.

Precedence, highest first: CLI flags (applied by the caller), environment variables
(``SCIS_HELP_`` prefix, ``__`` between levels, e.g. ``SCIS_HELP_CRAWL__DELAY_SECONDS``), a
``.env`` file in the working directory, built-in defaults. Secrets never go here; they belong in
``keyring``. Keep ``.env.example`` in step with every field.
"""

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CrawlSettings(BaseModel):
    base_url: str = "https://support.cci.drexel.edu/"
    # Seconds between requests; at least 1.0 so the crawler stays polite.
    delay_seconds: float = Field(default=1.5, ge=1.0)
    max_pages: int = Field(default=2000, ge=1)
    timeout_seconds: float = 20.0


class IndexSettings(BaseModel):
    embed_model: str = "BAAI/bge-small-en-v1.5"
    chunk_min_words: int = 150
    chunk_max_words: int = 450


class RetrievalSettings(BaseModel):
    k: int = Field(default=5, ge=1, le=20)
    candidates: int = 20  # per retriever, before fusion
    max_chunks_per_page: int = 2
    # The confidence gate (tuned on the dev split in payload 02): a search is confident when the
    # best dense cosine reaches min_score. BM25 scores grow with query length, so they don't
    # separate in- from out-of-scope questions; min_bm25 (None = off) is an optional extra way
    # through the gate for a lexical hit.
    min_score: float = 0.65
    min_bm25: float | None = None


class LlmSettings(BaseModel):
    enabled: bool = True
    model: str = "claude-haiku-5-5"
    effort: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = Field(default=600, ge=64, le=4096)
    max_output_tokens_desk: int = Field(default=900, ge=64, le=4096)
    daily_token_budget: int = Field(default=200_000, ge=1_000, le=10_000_000)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    # USD per million tokens for claude-haiku-5-5, prompts <= 100K (checked 2026-10-21).
    input_usd_per_mtok: float = 0.10
    output_usd_per_mtok: float = 0.50
    cache_read_usd_per_mtok: float = 0.01
    cache_write_usd_per_mtok: float = 0.125  # 5-minute cache writes


class AnswerSettings(BaseModel):
    help_contact: str = 'the help desk: https://support.cci.drexel.edu/ ("Talk to a person")'
    max_message_chars: int = Field(default=500, ge=50, le=4000)
    links_when_unavailable: int = 3


class WebSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_origins: list[str] = []  # CORS for embedding on another local page; empty = off
    rate_per_minute: int = Field(default=10, ge=1)  # per client IP
    session_max_messages: int = Field(default=20, ge=1)
    session_max_tokens: int = Field(default=40_000, ge=1_000)
    session_idle_minutes: int = Field(default=60, ge=5)
    max_sessions: int = Field(default=500, ge=10)  # oldest idle sessions evicted beyond this
    desk_local_only: bool = True  # /desk and mode=desk only from 127.0.0.1/::1


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCIS_HELP_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        # SCIS_HELP_HOME (paths.py) shares the prefix but is not a setting.
        extra="ignore",
    )

    crawl: CrawlSettings = CrawlSettings()
    index: IndexSettings = IndexSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    llm: LlmSettings = LlmSettings()
    answer: AnswerSettings = AnswerSettings()
    web: WebSettings = WebSettings()


def load_settings() -> Settings:
    """Settings from the environment and ``.env``; raises pydantic's ValidationError if invalid."""
    return Settings()
