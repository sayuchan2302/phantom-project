"""
Unit tests for Data Validator.
Verifies correct differentiation between market session breaks and data loss.
"""
from datetime import datetime, timezone
import pytest

from app.core.enums import DataQuality, Timeframe
from app.core.schemas import BarData
from app.data.data_validator import DataValidator


def create_sample_bar(dt: datetime, symbol: str = "XAUUSD", tf: Timeframe = Timeframe.M15) -> BarData:
    return BarData(
        symbol=symbol,
        timeframe=tf,
        time_utc=dt,
        open=2650.0,
        high=2655.0,
        low=2648.0,
        close=2652.0,
        tick_volume=100,
        spread=18,
        real_volume=0,
    )


def test_daily_session_break_detection():
    validator = DataValidator()

    # Normal trading day (Tuesday), gap from 20:55 to 23:05 UTC (Gold break)
    bar1 = create_sample_bar(datetime(2026, 10, 20, 20, 45, tzinfo=timezone.utc))
    bar2 = create_sample_bar(datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc))

    gaps = validator.analyze_gaps([bar1, bar2], Timeframe.M15)
    assert len(gaps) == 1
    assert gaps[0].is_session_break is True
    assert gaps[0].quality_status == DataQuality.SESSION_BREAK


def test_h1_daily_session_break_detection():
    validator = DataValidator()

    # On H1, last bar before break opens at 20:00 UTC and reopens at 23:00 UTC
    bar1 = create_sample_bar(datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc), tf=Timeframe.H1)
    bar2 = create_sample_bar(datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc), tf=Timeframe.H1)

    gaps = validator.analyze_gaps([bar1, bar2], Timeframe.H1)
    assert len(gaps) == 1
    assert gaps[0].is_session_break is True
    assert gaps[0].quality_status == DataQuality.SESSION_BREAK


def test_active_market_data_loss_detection():
    validator = DataValidator()

    # Gap during active London / NY session (14:00 to 16:00 UTC on Tuesday)
    bar1 = create_sample_bar(datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc))
    bar2 = create_sample_bar(datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc))

    gaps = validator.analyze_gaps([bar1, bar2], Timeframe.M15)
    assert len(gaps) == 1
    assert gaps[0].is_session_break is False
    assert gaps[0].quality_status == DataQuality.DATA_LOSS


def test_weekend_closure_detection():
    validator = DataValidator()

    # Friday night 20:45 UTC (2026-10-16) to Sunday night 23:00 UTC (2026-10-18)
    bar_fri = create_sample_bar(datetime(2026, 10, 16, 20, 45, tzinfo=timezone.utc))
    bar_sun = create_sample_bar(datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc))

    gaps = validator.analyze_gaps([bar_fri, bar_sun], Timeframe.M15)
    assert len(gaps) == 1
    assert gaps[0].is_session_break is True
    assert "Weekend" in gaps[0].explanation


def test_duplicate_timestamp_detection():
    validator = DataValidator()
    dt = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    bars = [create_sample_bar(dt), create_sample_bar(dt)]

    duplicates = validator.check_duplicates(bars)
    assert len(duplicates) == 1
    assert duplicates[0] == dt
