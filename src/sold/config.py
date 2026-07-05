"""Merkezi yapılandırma (ortam değişkenleri / .env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ortam değişkenlerinden okunan ayarlar.

    .env dosyası varsa otomatik okunur (bkz. .env.example).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # TCMB EVDS
    evds_api_key: str | None = None
    evds_base_url: str = "https://evds3.tcmb.gov.tr/igmevdsms-dis"

    # Veritabanı
    database_url: str = "sqlite:///sold.db"

    # Scraper
    scraper_user_agent: str = "sold-research-bot/0.1"
    scraper_min_delay: float = 3.0
    scraper_max_delay: float = 7.0


settings = Settings()
