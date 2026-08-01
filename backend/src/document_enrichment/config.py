from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    datahub_gms_url: AnyHttpUrl = "http://localhost:8080"
    datahub_graphql_url: AnyHttpUrl = "http://localhost:8080/api/graphql"
    datahub_token: str | None = None
    database_path: Path = Path("./data/document_enrichment.db")
    catalog_cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    cors_origins: str = "http://localhost:5173"
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: AnyHttpUrl = "https://api.openai.com/v1"
    http_timeout_seconds: float = Field(default=15, ge=1, le=60)
    max_upload_bytes: int = 256 * 1024
    max_document_characters: int = 30_000

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
