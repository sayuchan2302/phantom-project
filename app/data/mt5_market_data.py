"""
MT5 Market Data Collector and Processor.
Orchestrates collection, validation, and storage of bar and tick data.
"""
from typing import Dict, List, Optional
from app.core.enums import Timeframe
from app.core.schemas import BarData, GapAnalysis, SymbolCondition, TickData
from app.data.data_validator import DataValidator
from app.data.storage import SQLiteStorage
from app.execution.mt5_adapter import MT5AdapterReadOnly
from app.monitoring.logger import app_logger


class MarketDataService:
    """Service responsible for collecting, validating, and persisting market feeds."""

    def __init__(self, adapter: MT5AdapterReadOnly, storage: SQLiteStorage, validator: DataValidator):
        self.adapter = adapter
        self.storage = storage
        self.validator = validator

    def collect_and_store_bars(
        self,
        symbol: Optional[str] = None,
        timeframe: Timeframe = Timeframe.M15,
        count: int = 300,
    ) -> Dict[str, any]:
        """Fetch bars from MT5, validate continuity/session breaks, and upsert into SQLite."""
        sym = symbol or self.adapter.detect_gold_symbol()
        app_logger.info(f"Collecting {count} {timeframe.value} bars for {sym}...")

        bars = self.adapter.get_bars(symbol=sym, timeframe=timeframe, count=count)
        if not bars:
            app_logger.warning(f"No bars returned for {sym} ({timeframe.value})")
            return {"count": 0, "gaps": [], "duplicates": []}

        # Validate Continuity & Gaps
        gaps = self.validator.analyze_gaps(bars, timeframe)
        duplicates = self.validator.check_duplicates(bars)

        # Store in SQLite
        saved_count = self.storage.upsert_bars(bars)

        app_logger.info(
            f"Saved {saved_count} {timeframe.value} bars for {sym}. "
            f"Gaps found: {len(gaps)} (Session Breaks: {sum(1 for g in gaps if g.is_session_break)}, "
            f"Data Loss: {sum(1 for g in gaps if not g.is_session_break)}), Duplicates: {len(duplicates)}"
        )

        return {
            "count": saved_count,
            "gaps": gaps,
            "duplicates": duplicates,
            "first_bar_utc": bars[0].time_utc,
            "last_bar_utc": bars[-1].time_utc,
        }

    def collect_and_store_ticks(self, symbol: Optional[str] = None, count: int = 100) -> int:
        """Fetch and store latest ticks."""
        sym = symbol or self.adapter.detect_gold_symbol()
        ticks = self.adapter.get_ticks(symbol=sym, count=count)
        if ticks:
            saved = self.storage.upsert_ticks(ticks)
            app_logger.info(f"Saved {saved} ticks for {sym}")
            return saved
        return 0

    def record_live_condition(self, symbol: Optional[str] = None) -> SymbolCondition:
        """Read and log current live condition and spread."""
        sym = symbol or self.adapter.detect_gold_symbol()
        cond = self.adapter.get_symbol_condition(sym)
        self.storage.record_spread(cond)
        app_logger.info(
            f"Live {cond.name} | Bid: {cond.bid} | Ask: {cond.ask} | "
            f"Spread: {cond.spread_points} pts (~${cond.spread_usd}/lot)"
        )
        return cond
