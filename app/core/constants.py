"""
Core Constants for Exness Gold Trading System.
"""
from datetime import time

# Supported Symbol Candidates on Exness (Standard, Raw Spread, Pro)
DEFAULT_GOLD_SYMBOLS = ["XAUUSD", "XAUUSDm", "XAUUSD.m", "XAUUSDb"]

# Gold Daily Break & Session Times (UTC)
# Normal Gold daily session closes ~20:58/21:00 UTC and reopens ~23:01 UTC
GOLD_DAILY_BREAK_START_UTC = time(20, 58)
GOLD_DAILY_BREAK_END_UTC = time(23, 5)

# Weekend Break: Friday 21:00 UTC to Sunday 23:00 UTC
WEEKEND_CLOSE_WEEKDAY = 4  # Friday (Monday=0, Sunday=6)
WEEKEND_OPEN_WEEKDAY = 6   # Sunday

# Timeframe in minutes mapping
TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}
