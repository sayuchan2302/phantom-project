"""
SQLite Storage Engine for Market Data.
Guarantees idempotency (upsert) and historical price series reconstruction.
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import List, Optional

from app.config import settings
from app.core.enums import Timeframe
from app.core.schemas import BarData, SymbolCondition, TickData
from app.monitoring.logger import app_logger


class SQLiteStorage:
    """Handles persistence of market data, bar history, and spreads."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create necessary database tables with unique constraints."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Bars Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                time_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                tick_volume INTEGER NOT NULL,
                spread INTEGER NOT NULL,
                real_volume INTEGER NOT NULL,
                PRIMARY KEY (symbol, timeframe, time_utc)
            );
            """)

            # Ticks Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticks (
                symbol TEXT NOT NULL,
                time_utc TEXT NOT NULL,
                bid REAL NOT NULL,
                ask REAL NOT NULL,
                last REAL NOT NULL,
                volume REAL NOT NULL,
                flags INTEGER NOT NULL,
                PRIMARY KEY (symbol, time_utc, bid, ask)
            );
            """)

            # Spread History Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS spread_history (
                symbol TEXT NOT NULL,
                time_utc TEXT NOT NULL,
                bid REAL NOT NULL,
                ask REAL NOT NULL,
                spread_points INTEGER NOT NULL,
                spread_usd REAL NOT NULL,
                PRIMARY KEY (symbol, time_utc)
            );
            """)

            # Index for fast bar retrieval
            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bars_sym_tf_time 
            ON bars (symbol, timeframe, time_utc);
            """)

            conn.commit()
            app_logger.info(f"Database initialized at: {self.db_path}")

    def upsert_bars(self, bars: List[BarData]) -> int:
        """Upsert a list of bars avoiding duplicates."""
        if not bars:
            return 0

        query = """
        INSERT INTO bars (
            symbol, timeframe, time_utc, open, high, low, close, tick_volume, spread, real_volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, timeframe, time_utc) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            tick_volume=excluded.tick_volume,
            spread=excluded.spread,
            real_volume=excluded.real_volume;
        """

        records = [
            (
                b.symbol,
                b.timeframe.value,
                b.time_utc.isoformat(),
                b.open,
                b.high,
                b.low,
                b.close,
                b.tick_volume,
                b.spread,
                b.real_volume,
            )
            for b in bars
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(query, records)
            conn.commit()

        app_logger.debug(f"Upserted {len(bars)} bars into SQLite.")
        return len(bars)

    def upsert_ticks(self, ticks: List[TickData]) -> int:
        """Upsert ticks into database."""
        if not ticks:
            return 0

        query = """
        INSERT OR IGNORE INTO ticks (
            symbol, time_utc, bid, ask, last, volume, flags
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        records = [
            (
                t.symbol,
                t.time_utc.isoformat(),
                t.bid,
                t.ask,
                t.last,
                t.volume,
                t.flags,
            )
            for t in ticks
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(query, records)
            conn.commit()

        return len(ticks)

    def record_spread(self, cond: SymbolCondition) -> None:
        """Record a single spread point."""
        query = """
        INSERT OR REPLACE INTO spread_history (
            symbol, time_utc, bid, ask, spread_points, spread_usd
        ) VALUES (?, ?, ?, ?, ?, ?);
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (
                    cond.name,
                    cond.time_utc.isoformat(),
                    cond.bid,
                    cond.ask,
                    cond.spread_points,
                    cond.spread_usd,
                ),
            )
            conn.commit()

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 500,
        start_utc: Optional[datetime] = None,
        end_utc: Optional[datetime] = None,
    ) -> List[BarData]:
        """Query bars from database to reconstruct price series."""
        query = "SELECT * FROM bars WHERE symbol = ? AND timeframe = ?"
        params = [symbol, timeframe.value]

        if start_utc:
            query += " AND time_utc >= ?"
            params.append(start_utc.isoformat())
        if end_utc:
            query += " AND time_utc <= ?"
            params.append(end_utc.isoformat())

        query += " ORDER BY time_utc ASC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

        bars: List[BarData] = []
        for r in rows:
            dt = datetime.fromisoformat(r["time_utc"])
            bars.append(
                BarData(
                    symbol=r["symbol"],
                    timeframe=Timeframe(r["timeframe"]),
                    time_utc=dt,
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    tick_volume=r["tick_volume"],
                    spread=r["spread"],
                    real_volume=r["real_volume"],
                )
            )
        return bars

    def count_bars(self, symbol: str, timeframe: Timeframe) -> int:
        """Count total bars stored."""
        query = "SELECT COUNT(*) as count FROM bars WHERE symbol = ? AND timeframe = ?"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (symbol, timeframe.value))
            res = cursor.fetchone()
            return res["count"] if res else 0
