"""
Unit tests for SQLite Storage.
"""
from datetime import datetime, timezone
from pathlib import Path
import pytest
from app.core.enums import Timeframe
from app.core.schemas import BarData
from app.data.storage import SQLiteStorage


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_market_data.db"
    return SQLiteStorage(db_path=db_file)


def test_upsert_and_reconstruct_bars(temp_db: SQLiteStorage):
    dt1 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 10, 20, 10, 15, tzinfo=timezone.utc)

    bar1 = BarData(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        time_utc=dt1,
        open=2650.0,
        high=2655.0,
        low=2648.0,
        close=2652.0,
        tick_volume=120,
        spread=15,
        real_volume=0,
    )
    bar2 = BarData(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        time_utc=dt2,
        open=2652.0,
        high=2658.0,
        low=2651.0,
        close=2656.0,
        tick_volume=150,
        spread=16,
        real_volume=0,
    )

    # Insert
    inserted = temp_db.upsert_bars([bar1, bar2])
    assert inserted == 2
    assert temp_db.count_bars("XAUUSD", Timeframe.M15) == 2

    # Upsert with modified close for bar2
    bar2_modified = BarData(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        time_utc=dt2,
        open=2652.0,
        high=2658.0,
        low=2651.0,
        close=2657.5,  # modified
        tick_volume=160,
        spread=16,
        real_volume=0,
    )
    temp_db.upsert_bars([bar2_modified])

    # Count should still be 2 (no duplicate row created)
    assert temp_db.count_bars("XAUUSD", Timeframe.M15) == 2

    # Query back
    bars = temp_db.get_bars("XAUUSD", Timeframe.M15)
    assert len(bars) == 2
    assert bars[1].close == 2657.5
