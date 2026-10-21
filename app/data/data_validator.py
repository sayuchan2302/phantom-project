"""
Data Validator for Market Feeds.
Distinguishes between expected session breaks (daily Gold maintenance / weekend)
and actual data loss (unexpected missing bars).
"""
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Tuple

from app.core.constants import (
    GOLD_DAILY_BREAK_END_UTC,
    GOLD_DAILY_BREAK_START_UTC,
    TIMEFRAME_MINUTES,
    WEEKEND_CLOSE_WEEKDAY,
    WEEKEND_OPEN_WEEKDAY,
)
from app.core.enums import DataQuality, Timeframe
from app.core.schemas import BarData, GapAnalysis
from app.monitoring.logger import app_logger


class DataValidator:
    """Validates bar continuity, timestamps, and detects data loss vs market breaks."""

    @staticmethod
    def is_weekend_break(start_utc: datetime, end_utc: datetime) -> bool:
        """Return True if the gap is across Friday night to Sunday evening."""
        # Start is Friday >= 20:45 UTC and end is Sunday >= 22:45 UTC
        if start_utc.weekday() == WEEKEND_CLOSE_WEEKDAY and end_utc.weekday() == WEEKEND_OPEN_WEEKDAY:
            return True
        # Or spans Friday through Monday morning
        if start_utc.weekday() == WEEKEND_CLOSE_WEEKDAY and end_utc.weekday() == 0:
            return True
        return False

    @staticmethod
    def is_daily_session_break(start_utc: datetime, end_utc: datetime) -> bool:
        """
        Return True if gap matches Gold daily maintenance (~20:55 to 23:05 UTC).
        """
        # Usually gap starts after 20:55 and ends after 23:00 on the same date or next early hour
        start_time = start_utc.time()
        end_time = end_utc.time()

        # For H1, bar opens at 20:00 UTC. For M15/M5, bar opens at 20:45 or 20:50/20:55 UTC.
        is_break_start = (time(20, 0) <= start_time <= time(21, 15))
        # Check if end is around market reopen window (22:50 - 23:15 UTC)
        is_break_end = (time(22, 50) <= end_time <= time(23, 15))

        # Duration is ~2 to 3 hours (100 mins for M15/M5, 180 mins for H1)
        duration_minutes = (end_utc - start_utc).total_seconds() / 60.0
        if is_break_start and is_break_end and (90 <= duration_minutes <= 195):
            return True

        return False

    def analyze_gaps(self, bars: List[BarData], timeframe: Timeframe) -> List[GapAnalysis]:
        """Scan list of bars in chronological order and identify any gaps."""
        if len(bars) < 2:
            return []

        sorted_bars = sorted(bars, key=lambda b: b.time_utc)
        step_minutes = TIMEFRAME_MINUTES[timeframe.value]
        expected_delta = timedelta(minutes=step_minutes)

        gaps: List[GapAnalysis] = []

        for i in range(len(sorted_bars) - 1):
            curr_bar = sorted_bars[i]
            next_bar = sorted_bars[i + 1]

            actual_delta = next_bar.time_utc - curr_bar.time_utc
            if actual_delta > expected_delta:
                missing_minutes = (actual_delta - expected_delta).total_seconds() / 60.0
                missing_bar_count = int(missing_minutes // step_minutes)

                # Check whether this is a legitimate session break
                is_weekend = self.is_weekend_break(curr_bar.time_utc, next_bar.time_utc)
                is_daily = self.is_daily_session_break(curr_bar.time_utc, next_bar.time_utc)

                if is_weekend or is_daily:
                    reason = "Weekend Market Closure" if is_weekend else "Daily Gold Maintenance Break"
                    gaps.append(
                        GapAnalysis(
                            symbol=curr_bar.symbol,
                            timeframe=timeframe,
                            gap_start_utc=curr_bar.time_utc,
                            gap_end_utc=next_bar.time_utc,
                            missing_bars=missing_bar_count,
                            quality_status=DataQuality.SESSION_BREAK,
                            is_session_break=True,
                            explanation=f"Valid {reason} ({missing_bar_count} bars)",
                        )
                    )
                else:
                    gaps.append(
                        GapAnalysis(
                            symbol=curr_bar.symbol,
                            timeframe=timeframe,
                            gap_start_utc=curr_bar.time_utc,
                            gap_end_utc=next_bar.time_utc,
                            missing_bars=missing_bar_count,
                            quality_status=DataQuality.DATA_LOSS,
                            is_session_break=False,
                            explanation=f"UNEXPECTED GAP during open trading hours! Missing ~{missing_bar_count} bars",
                        )
                    )

        return gaps

    def check_duplicates(self, bars: List[BarData]) -> List[datetime]:
        """Detect any duplicate timestamps in bars."""
        seen = set()
        duplicates = []
        for bar in bars:
            if bar.time_utc in seen:
                duplicates.append(bar.time_utc)
            seen.add(bar.time_utc)
        return duplicates
