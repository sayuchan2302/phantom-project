"""
Comprehensive Unit Tests for Phase 2 - Intelligence & Feature Engineering.
"""
from datetime import datetime, timezone
from pathlib import Path
import pytest
import pandas as pd

from app.core.enums import EventStatus, NewsImpact, Timeframe, TrendState
from app.core.schemas import BarData, EconomicEvent, SymbolCondition
from app.data.economic_calendar import EconomicCalendarCollector
from app.intelligence.feature_store import FeatureStore
from app.intelligence.market_analyzer import MarketAnalyzer
from app.intelligence.news_extractor import NewsExtractor


def make_bars(count: int = 50, trend: str = "up") -> list[BarData]:
    bars = []
    base_price = 2600.0
    for i in range(count):
        dt = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
        step = i * 2.0 if trend == "up" else -i * 2.0
        close = base_price + step
        bars.append(
            BarData(
                symbol="XAUUSDm",
                timeframe=Timeframe.M15,
                time_utc=dt,
                open=close - 1.0,
                high=close + 2.0,
                low=close - 2.0,
                close=close,
                tick_volume=100,
                spread=20,
                real_volume=0,
            )
        )
    return bars


def test_market_analyzer_indicators():
    analyzer = MarketAnalyzer()
    bars = make_bars(60, trend="up")
    cond = SymbolCondition(
        name="XAUUSDm",
        digits=3,
        point=0.001,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        bid=2719.0,
        ask=2719.2,
        spread_points=20,
        spread_usd=2.0,
        time_utc=datetime.now(timezone.utc),
    )

    features = analyzer.analyze(symbol="XAUUSDm", bars_m15=bars, bars_h1=bars, current_condition=cond)
    assert features.close > 0
    assert features.ema_21_m15 > 0
    assert features.ema_50_m15 > 0
    assert features.atr_14_m15 > 0
    assert features.spread_ratio > 0
    assert features.trend_state in [TrendState.STRONG_UP, TrendState.UP]


def test_economic_calendar_number_parsing():
    parser = EconomicCalendarCollector._parse_float
    assert parser("3.2%") == 3.2
    assert parser("215K") == 215000.0
    assert parser("1.5M") == 1500000.0
    assert parser("-0.5%") == -0.5
    assert parser("N/A") is None
    assert parser("") is None


def test_news_extractor_surprise_score_deterministic():
    extractor = NewsExtractor(api_key=None)

    # Actual 3.5% vs Forecast 3.0% -> (3.5 - 3.0) / 3.0 = 0.1667
    score = extractor.calculate_surprise_score(actual=3.5, forecast=3.0)
    assert score == pytest.approx(0.1667, rel=1e-3)

    # Actual 2.8% vs Forecast 3.0% -> (2.8 - 3.0) / 3.0 = -0.0667
    score_neg = extractor.calculate_surprise_score(actual=2.8, forecast=3.0)
    assert score_neg == pytest.approx(-0.0667, rel=1e-3)

    # None values
    assert extractor.calculate_surprise_score(None, 3.0) is None
    assert extractor.calculate_surprise_score(3.0, None) is None


def test_news_extractor_graceful_degradation():
    # Pass dummy invalid API key to ensure fallback works and does not raise exception
    extractor = NewsExtractor(api_key="INVALID_DUMMY_KEY_FOR_TESTING")
    event = EconomicEvent(
        id=1,
        time_utc=datetime.now(timezone.utc),
        currency="USD",
        name="CPI m/m",
        importance=NewsImpact.HIGH,
        actual=0.4,
        forecast=0.2,
        previous=0.2,
        status=EventStatus.JUST_RELEASED,
    )

    result = extractor.extract_from_event(event)
    assert result.event_name == "CPI m/m"
    assert result.actual == 0.4
    assert result.surprise_score == 1.0  # (0.4 - 0.2) / 0.2 = 1.0
    assert len(result.qualitative_impact) > 0


def test_feature_store_persistence_and_latency(tmp_path: Path):
    db_file = tmp_path / "test_features.db"
    store = FeatureStore(db_path=db_file)

    analyzer = MarketAnalyzer()
    bars = make_bars(40, trend="up")
    cond = SymbolCondition(
        name="XAUUSDm",
        digits=3,
        point=0.001,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        bid=2679.0,
        ask=2679.2,
        spread_points=20,
        spread_usd=2.0,
        time_utc=datetime.now(timezone.utc),
    )
    tech = analyzer.analyze("XAUUSDm", bars, bars, cond)

    # Save
    import time
    start = time.perf_counter()
    vec = store.build_and_save_features(technical=tech, start_clock=start)

    assert vec.calculation_latency_ms >= 0.0
    rows = store.get_latest_features("XAUUSDm", limit=1)
    assert len(rows) == 1
    assert rows[0]["trend_state"] in [TrendState.STRONG_UP.value, TrendState.UP.value]
    assert rows[0]["calculation_latency_ms"] >= 0.0
