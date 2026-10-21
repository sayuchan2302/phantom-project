"""
Feature Store for Centralized Features Persistence & Latency Tracking.
Stores unified technical and macroeconomic features for Phase 3 Prediction Model.
"""
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.config import settings
from app.core.enums import TrendState
from app.core.schemas import (
    NewsExtractionResult,
    TechnicalFeatures,
    UnifiedFeatureVector,
)
from app.monitoring.logger import app_logger


class FeatureStore:
    """Stores feature vectors into SQLite database with latency tracking."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create features table with comprehensive indexing."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS feature_store (
                symbol TEXT NOT NULL,
                time_utc TEXT NOT NULL,
                close REAL NOT NULL,
                trend_state TEXT NOT NULL,
                ema_21_m15 REAL NOT NULL,
                ema_50_m15 REAL NOT NULL,
                ema_200_m15 REAL NOT NULL,
                atr_14_m15 REAL NOT NULL,
                current_spread INTEGER NOT NULL,
                spread_ratio REAL NOT NULL,
                latest_event_name TEXT,
                surprise_score REAL,
                qualitative_impact TEXT,
                minutes_since_event REAL,
                calculation_latency_ms REAL NOT NULL,
                created_at_utc TEXT NOT NULL,
                PRIMARY KEY (symbol, time_utc)
            );
            """)
            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_features_sym_time 
            ON feature_store (symbol, time_utc);
            """)
            conn.commit()

    def build_and_save_features(
        self,
        technical: TechnicalFeatures,
        latest_news: Optional[NewsExtractionResult] = None,
        event_time_utc: Optional[datetime] = None,
        start_clock: Optional[float] = None,
    ) -> UnifiedFeatureVector:
        """Construct unified feature vector, measure latency, and persist."""
        latency_ms = (time.perf_counter() - start_clock) * 1000.0 if start_clock else 0.0

        minutes_since_event = None
        if event_time_utc:
            delta = technical.time_utc - event_time_utc
            minutes_since_event = round(delta.total_seconds() / 60.0, 1)

        vector = UnifiedFeatureVector(
            symbol=technical.symbol,
            time_utc=technical.time_utc,
            technical=technical,
            latest_news=latest_news,
            minutes_since_last_event=minutes_since_event,
            calculation_latency_ms=round(latency_ms, 2),
        )

        query = """
        INSERT INTO feature_store (
            symbol, time_utc, close, trend_state, ema_21_m15, ema_50_m15, ema_200_m15,
            atr_14_m15, current_spread, spread_ratio, latest_event_name, surprise_score,
            qualitative_impact, minutes_since_event, calculation_latency_ms, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, time_utc) DO UPDATE SET
            close=excluded.close,
            trend_state=excluded.trend_state,
            ema_21_m15=excluded.ema_21_m15,
            ema_50_m15=excluded.ema_50_m15,
            ema_200_m15=excluded.ema_200_m15,
            atr_14_m15=excluded.atr_14_m15,
            current_spread=excluded.current_spread,
            spread_ratio=excluded.spread_ratio,
            latest_event_name=excluded.latest_event_name,
            surprise_score=excluded.surprise_score,
            qualitative_impact=excluded.qualitative_impact,
            minutes_since_event=excluded.minutes_since_event,
            calculation_latency_ms=excluded.calculation_latency_ms,
            created_at_utc=excluded.created_at_utc;
        """

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (
                    vector.symbol,
                    vector.time_utc.isoformat(),
                    technical.close,
                    technical.trend_state.value,
                    technical.ema_21_m15,
                    technical.ema_50_m15,
                    technical.ema_200_m15,
                    technical.atr_14_m15,
                    technical.current_spread,
                    technical.spread_ratio,
                    latest_news.event_name if latest_news else None,
                    latest_news.surprise_score if latest_news else None,
                    latest_news.qualitative_impact if latest_news else None,
                    minutes_since_event,
                    vector.calculation_latency_ms,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

        app_logger.info(
            f"Saved Unified Features [{vector.symbol}]: Trend={technical.trend_state.value}, "
            f"Latency={vector.calculation_latency_ms:.2f}ms"
        )
        return vector

    def get_latest_features(self, symbol: str, limit: int = 10) -> List[dict]:
        """Fetch latest features from store."""
        query = "SELECT * FROM feature_store WHERE symbol = ? ORDER BY time_utc DESC LIMIT ?"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (symbol, limit))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
