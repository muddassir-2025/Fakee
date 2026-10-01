"""Application configuration.

All external services are optional. When a key is absent the corresponding
service falls back to a deterministic mock so the app always runs end-to-end.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "Fake Job / Internship Detector"
    environment: str = "development"
    api_prefix: str = "/api"
    log_level: str = "INFO"

    # Comma-separated list of allowed frontend origins.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Database (Neon PostgreSQL in production, SQLite locally) ---
    # e.g. postgresql+asyncpg://user:pass@host/db?ssl=require
    database_url: str = "sqlite+aiosqlite:///./data/investigations.db"

    # --- Groq (LLM extraction + evidence structuring) ---
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 45.0

    # --- Bright Data (search + scrape) ---
    brightdata_api_key: str | None = None
    brightdata_serp_zone: str = "serp_api1"
    brightdata_unlocker_zone: str = "web_unlocker1"
    brightdata_base_url: str = "https://api.brightdata.com"
    brightdata_timeout_seconds: float = 45.0

    # --- Investigation tuning ---
    max_search_queries: int = 18
    max_scrape_pages: int = 12
    search_results_per_query: int = 6
    domain_recent_days: int = 180

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def brightdata_enabled(self) -> bool:
        return bool(self.brightdata_api_key)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
