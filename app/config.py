"""
Configuration management using pydantic-settings.
"""
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MT5 Credentials
    MT5_LOGIN: int
    MT5_PASSWORD: str
    MT5_SERVER: str
    MT5_PATH: Optional[str] = None  # Optional custom path to terminal64.exe

    # Symbol Configuration
    TARGET_SYMBOL: str = "XAUUSD"

    # Gemini API
    GEMINI_API_KEY: Optional[str] = None

    # Telegram Bot Alerts (Phase 5)
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Circuit Breaker & Monitoring (Phase 5)
    CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES: int = 5
    CIRCUIT_BREAKER_DISCONNECT_TIMEOUT_SEC: int = 60
    KILL_SWITCH_FLAG_FILE: Path = Path("kill_switch.flag")

    # Storage & Logging
    DATABASE_PATH: Path = Path("data/market_data.db")
    LOG_LEVEL: str = "INFO"
    LOG_DIR: Path = Path("logs")


settings = Settings()
