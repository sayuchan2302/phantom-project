"""
Logging setup using Loguru with UTC timestamps and rotation.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from app.config import settings


def patch_utc(record):
    """Ensure timestamps in log records are strictly UTC."""
    record["extra"]["utc_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def setup_logger():
    """Configure loguru logger for console and file rotation."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logger.remove()

    log_format = (
        "<green>{extra[utc_time]} UTC</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # Console Output
    logger.add(
        sys.stdout,
        format=log_format,
        level=settings.LOG_LEVEL,
        colorize=True,
    )

    # File Output with Rotation
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_path = log_dir / "trading_bot_{time:YYYY-MM-DD}.log"

    logger.add(
        str(file_path),
        format=log_format,
        level=settings.LOG_LEVEL,
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
    )

    return logger.patch(patch_utc)


app_logger = setup_logger()
