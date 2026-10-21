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

    # Strategy & Scalping Tuning (Ăn chắc mặc bền - Đánh nhanh ăn nhỏ)
    ATR_TP_MULTIPLIER: float = 0.5  # Scalping TP siêu ngắn: ~2.0 - 2.5 giá vàng (~$2.0 / 0.01 lot)
    ATR_SL_MULTIPLIER: float = 0.5  # Scalping SL siêu ngắn: ~2.0 - 2.5 giá vàng (không bao giờ để âm sâu)
    MIN_WIN_PROBABILITY: float = 0.52 # Ngưỡng xác suất tối thiểu cho scalping
    AUTO_CLOSE_PROFIT_USD: float = 2.0 # Tự động cắt chốt lời ngay khi lệnh lãi >= $2.00
    AUTO_CLOSE_LOSS_USD: float = 2.0   # Tự động cắt lỗ khẩn cấp ngay khi lệnh âm >= -$2.00 (Bảo toàn vốn)
    MAX_OPEN_POSITIONS: int = 1        # Giai đoạn train: Đánh 1 lệnh duy nhất dứt điểm để quan sát & học
    MAX_DAILY_LOSS_PCT: float = 0.05   # Giới hạn lỗ tối đa ngày: 5.0% vốn (~$5.00)

    # Storage & Logging
    DATABASE_PATH: Path = Path("data/market_data.db")
    LOG_LEVEL: str = "INFO"
    LOG_DIR: Path = Path("logs")


settings = Settings()
