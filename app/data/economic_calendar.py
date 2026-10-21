"""
Economic Calendar Collector for USD / Macroeconomic News.
Fetches high-impact economic events, normalizes timestamps to UTC,
and classifies event status (UPCOMING, JUST_RELEASED, PAST).
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import requests

from app.core.enums import EventStatus, NewsImpact
from app.core.schemas import EconomicEvent
from app.monitoring.logger import app_logger


CALENDAR_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

KEY_GOLD_EVENTS = [
    "cpi",
    "nfp",
    "non-farm",
    "fomc",
    "interest rate",
    "fed",
    "gdp",
    "retail sales",
    "ppi",
    "unemployment",
    "jobless claims",
    "pmi",
    "ism",
]


class EconomicCalendarCollector:
    """Collects and normalizes macroeconomic calendar events."""

    def __init__(self, feed_url: str = CALENDAR_FEED_URL):
        self.feed_url = feed_url

    @staticmethod
    def _parse_float(val: Optional[str]) -> Optional[float]:
        """Safely clean and parse number string from calendar (e.g. '3.2%', '215K', '1.5M')."""
        if not val or val.strip() in ("", "-", "N/A"):
            return None
        cleaned = val.replace("%", "").replace(",", "").strip()
        multiplier = 1.0
        if cleaned.endswith("K") or cleaned.endswith("k"):
            multiplier = 1_000.0
            cleaned = cleaned[:-1]
        elif cleaned.endswith("M") or cleaned.endswith("m"):
            multiplier = 1_000_000.0
            cleaned = cleaned[:-1]
        elif cleaned.endswith("B") or cleaned.endswith("b"):
            multiplier = 1_000_000_000.0
            cleaned = cleaned[:-1]

        try:
            return round(float(cleaned) * multiplier, 4)
        except ValueError:
            return None

    @staticmethod
    def _map_impact(impact_str: str) -> NewsImpact:
        mapping = {
            "high": NewsImpact.HIGH,
            "medium": NewsImpact.MEDIUM,
            "low": NewsImpact.LOW,
        }
        return mapping.get(impact_str.lower(), NewsImpact.NONE)

    def fetch_events(self, currency: str = "USD", high_impact_only: bool = False) -> List[EconomicEvent]:
        """
        Fetch calendar events for the week, filtered by currency and optionally impact.
        """
        try:
            headers = {"User-Agent": "ModularTradingBot/1.0"}
            resp = requests.get(self.feed_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                app_logger.warning(f"Calendar feed returned status code {resp.status_code}")
                return self._get_fallback_events()

            raw_data = resp.json()
        except Exception as e:
            app_logger.warning(f"Failed to fetch remote calendar feed: {e}. Using fallback data.")
            return self._get_fallback_events()

        now_utc = datetime.now(timezone.utc)
        events: List[EconomicEvent] = []

        for idx, item in enumerate(raw_data):
            if item.get("country") != currency:
                continue

            impact = self._map_impact(item.get("impact", ""))
            if high_impact_only and impact != NewsImpact.HIGH:
                continue

            # Parse ISO date with timezone offset into UTC
            date_str = item.get("date", "")
            try:
                dt = datetime.fromisoformat(date_str)
                dt_utc = dt.astimezone(timezone.utc)
            except Exception:
                continue

            # Classify status
            delta = now_utc - dt_utc
            if delta < timedelta(0):
                status = EventStatus.UPCOMING
            elif timedelta(0) <= delta <= timedelta(minutes=30):
                status = EventStatus.JUST_RELEASED
            else:
                status = EventStatus.PAST

            event = EconomicEvent(
                id=idx + 1,
                time_utc=dt_utc,
                currency=currency,
                name=item.get("title", "Unnamed Event"),
                importance=impact,
                actual=self._parse_float(item.get("actual")),
                forecast=self._parse_float(item.get("forecast")),
                previous=self._parse_float(item.get("previous")),
                status=status,
            )
            events.append(event)

        app_logger.info(f"Loaded {len(events)} {currency} calendar events (High impact: {sum(1 for e in events if e.importance == NewsImpact.HIGH)})")
        return events

    def get_upcoming_high_impact_events(self, window_hours: float = 24.0) -> List[EconomicEvent]:
        """Return upcoming high-impact USD events in the next X hours."""
        events = self.fetch_events(currency="USD", high_impact_only=True)
        now_utc = datetime.now(timezone.utc)
        max_time = now_utc + timedelta(hours=window_hours)

        upcoming = [e for e in events if now_utc <= e.time_utc <= max_time]
        return upcoming

    def _get_fallback_events(self) -> List[EconomicEvent]:
        """Fallback mock events in case network is down."""
        now_utc = datetime.now(timezone.utc)
        return [
            EconomicEvent(
                id=999,
                time_utc=now_utc - timedelta(hours=2),
                currency="USD",
                name="Core CPI m/m",
                importance=NewsImpact.HIGH,
                actual=0.3,
                forecast=0.2,
                previous=0.2,
                status=EventStatus.PAST,
            )
        ]
