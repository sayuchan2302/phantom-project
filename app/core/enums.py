"""
Domain Enumerations for Modular Trading System.
"""
from enum import Enum


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


class SessionState(str, Enum):
    OPEN = "OPEN"
    PRE_CLOSE = "PRE_CLOSE"      # 15 mins before daily break or weekend
    DAILY_BREAK = "DAILY_BREAK"  # Gold daily maintenance break (~21:00 - 23:00 UTC)
    WEEKEND_CLOSED = "WEEKEND_CLOSED"


class DataQuality(str, Enum):
    VALID = "VALID"
    SESSION_BREAK = "SESSION_BREAK"  # Expected gap due to market hours
    DATA_LOSS = "DATA_LOSS"          # Unexpected gap (missing bars)
    DUPLICATE = "DUPLICATE"
    TIME_DRIFT = "TIME_DRIFT"


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


class TrendState(str, Enum):
    STRONG_UP = "STRONG_UP"
    UP = "UP"
    RANGE = "RANGE"
    DOWN = "DOWN"
    STRONG_DOWN = "STRONG_DOWN"


class NewsImpact(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class EventStatus(str, Enum):
    UPCOMING = "UPCOMING"
    JUST_RELEASED = "JUST_RELEASED"
    PAST = "PAST"


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    STAND_ASIDE = "STAND_ASIDE"


class TradeAction(str, Enum):
    ENTRY_BUY = "ENTRY_BUY"
    ENTRY_SELL = "ENTRY_SELL"
    EXIT_SL = "EXIT_SL"
    EXIT_TP = "EXIT_TP"
    HOLD = "HOLD"


class RiskDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED_DAILY_LOSS = "REJECTED_DAILY_LOSS"
    REJECTED_SPREAD = "REJECTED_SPREAD"
    REJECTED_MARGIN = "REJECTED_MARGIN"
    REJECTED_SESSION_GUARD = "REJECTED_SESSION_GUARD"
    REJECTED_NEWS_SHIELD = "REJECTED_NEWS_SHIELD"
    REJECTED_MAX_POSITIONS = "REJECTED_MAX_POSITIONS"
    REJECTED_INVALID_LOT = "REJECTED_INVALID_LOT"



