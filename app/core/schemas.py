"""
Data Contracts (Pydantic v2 schemas) for System Entities.
"""
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)

from app.core.enums import (
    Timeframe,
    DataQuality,
    SessionState,
    TrendState,
    NewsImpact,
    EventStatus,
    SignalType,
    TradeAction,
    RiskDecision,
)


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AccountInfo(BaseSchema):
    login: int
    server: str
    currency: str = "USD"
    balance: float
    equity: float
    profit: float
    margin: float
    margin_free: float
    margin_level: Optional[float] = None
    leverage: int
    timestamp_utc: datetime = Field(default_factory=utc_now)


class SymbolCondition(BaseSchema):
    name: str
    digits: int
    point: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    bid: float
    ask: float
    spread_points: int
    spread_usd: float  # Spread cost per 1.0 standard lot
    time_utc: datetime


class TickData(BaseSchema):
    symbol: str
    time_utc: datetime
    bid: float
    ask: float
    last: float = 0.0
    volume: float = 0.0
    flags: int = 0


class BarData(BaseSchema):
    symbol: str
    timeframe: Timeframe
    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int = 0


class GapAnalysis(BaseSchema):
    symbol: str
    timeframe: Timeframe
    gap_start_utc: datetime
    gap_end_utc: datetime
    missing_bars: int
    quality_status: DataQuality
    is_session_break: bool
    explanation: str


class TechnicalFeatures(BaseSchema):
    symbol: str
    time_utc: datetime
    close: float
    ema_21_m15: float
    ema_50_m15: float
    ema_200_m15: float
    ema_21_h1: float
    ema_50_h1: float
    ema_200_h1: float
    atr_14_m15: float
    current_spread: int
    spread_ema_30m: float
    spread_ratio: float  # current_spread / spread_ema_30m (> 2.0 indicates abnormal liquidity)
    trend_state: TrendState


class EconomicEvent(BaseSchema):
    id: int
    time_utc: datetime
    currency: str
    name: str
    importance: NewsImpact
    actual: Optional[float] = None
    forecast: Optional[float] = None
    previous: Optional[float] = None
    status: EventStatus


class NewsExtractionResult(BaseSchema):
    event_name: str
    time_utc: datetime
    actual: Optional[float] = None
    forecast: Optional[float] = None
    previous: Optional[float] = None
    surprise_score: Optional[float] = None  # (Actual - Forecast) / |Forecast| (calculated in Python code)
    qualitative_impact: str
    source_model: str = "gemini"


class UnifiedFeatureVector(BaseSchema):
    symbol: str
    time_utc: datetime
    technical: TechnicalFeatures
    latest_news: Optional[NewsExtractionResult] = None
    minutes_since_last_event: Optional[float] = None
    calculation_latency_ms: float


class TradeSignal(BaseSchema):
    symbol: str
    time_utc: datetime
    signal_type: SignalType
    win_probability: float  # Calibrated probability from model
    expected_value: float   # EV after spread, slippage, commission
    suggested_sl: float
    suggested_tp: float
    reason: str


class BacktestTrade(BaseSchema):
    symbol: str
    entry_time_utc: datetime
    exit_time_utc: datetime
    signal_type: SignalType
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    lot_size: float = 0.1
    gross_pnl_usd: float
    spread_cost_usd: float
    slippage_cost_usd: float
    commission_usd: float
    net_pnl_usd: float
    exit_reason: str


class BacktestReport(BaseSchema):
    strategy_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_net_profit_usd: float
    avg_trade_pnl: float


class RiskAssessment(BaseSchema):
    decision: RiskDecision
    approved: bool
    calculated_lot: float
    max_risk_usd: float
    reason: str
    daily_drawdown_pct: float


class OrderRequest(BaseSchema):
    symbol: str
    signal_type: SignalType
    volume: float
    price: float
    sl: float
    tp: float
    magic_number: int
    comment: str = "AI_MODULAR_BOT"


class OrderResult(BaseSchema):
    success: bool
    ticket: Optional[int] = None
    volume: float = 0.0
    price: float = 0.0
    retcode: int = 0
    comment: str = ""
    magic_number: int = 0
    timestamp_utc: datetime = Field(default_factory=utc_now)


class PositionInfo(BaseSchema):
    ticket: int
    symbol: str
    signal_type: SignalType
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    magic: int
    time_utc: datetime



