"""
Risk Manager - Core Veto & Capital Preservation Engine.
Enforces multi-layer defensive filters before any trade is dispatched to broker.
"""
from datetime import datetime, time, timedelta, timezone
from typing import List, Optional, Tuple
import math

from app.core.constants import WEEKEND_CLOSE_WEEKDAY
from app.core.enums import NewsImpact, RiskDecision, SignalType
from app.core.schemas import (
    AccountInfo,
    EconomicEvent,
    PositionInfo,
    RiskAssessment,
    SymbolCondition,
    TechnicalFeatures,
    TradeSignal,
)
from app.monitoring.logger import app_logger


class RiskManager:
    """Enforces position sizing, loss limits, spread guard, session guard, and news shield."""

    def __init__(
        self,
        risk_per_trade_pct: float = 0.005,  # 0.5% capital risk per trade
        max_daily_loss_pct: float = 0.02,   # 2.0% max daily loss
        max_absolute_spread_pts: int = 400, # 40 pips on Gold
        max_open_positions: int = 2,
    ):
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_absolute_spread_pts = max_absolute_spread_pts
        self.max_open_positions = max_open_positions

    def calculate_lot_size(
        self,
        balance: float,
        entry_price: float,
        sl_price: float,
        symbol_cond: SymbolCondition,
    ) -> float:
        """
        Calculate Lot size based on physical Stop Loss distance and risk percentage:
        Lot = (Balance * RiskPct) / (|Entry - SL| * ContractSize)
        """
        risk_usd = balance * self.risk_per_trade_pct
        sl_distance = abs(entry_price - sl_price)

        if sl_distance <= 0:
            return symbol_cond.volume_min

        # For XAUUSD: 1 point price diff * contract_size (100)
        lot = risk_usd / (sl_distance * symbol_cond.contract_size)

        # Round down to volume step
        step = symbol_cond.volume_step
        lot_rounded = math.floor(lot / step) * step
        lot_clamped = max(symbol_cond.volume_min, min(symbol_cond.volume_max, lot_rounded))
        return round(lot_clamped, 2)

    def is_session_guard_active(self, dt_utc: datetime) -> Tuple[bool, str]:
        """
        Check if current time is inside dangerous Gold session break or weekend window.
        1. Daily maintenance break: 20:45 to 23:05 UTC (Monday - Thursday).
        2. Weekend close: Friday after 20:30 UTC until Sunday 23:05 UTC.
        """
        t = dt_utc.time()
        weekday = dt_utc.weekday()

        # Friday night pre-weekend close guard
        if weekday == WEEKEND_CLOSE_WEEKDAY and t >= time(20, 30):
            return True, "Friday pre-weekend close (no holding over weekend)"
        if weekday in [5, 6] and not (weekday == 6 and t >= time(23, 5)):
            return True, "Weekend market closed"

        # Daily break guard (20:45 to 23:05 UTC)
        if time(20, 45) <= t <= time(23, 5):
            return True, "Daily Gold session maintenance break (20:45 - 23:05 UTC)"

        return False, ""

    def is_news_shield_active(self, dt_utc: datetime, upcoming_events: List[EconomicEvent]) -> Tuple[bool, str]:
        """
        News Shield: Block entries 30 mins before and 5 mins after high-impact USD events.
        """
        for ev in upcoming_events:
            if ev.currency == "USD" and ev.importance == NewsImpact.HIGH:
                pre_news = ev.time_utc - timedelta(minutes=30)
                post_news = ev.time_utc + timedelta(minutes=5)
                if pre_news <= dt_utc <= post_news:
                    return True, f"News Shield Active: High-impact event '{ev.name}' at {ev.time_utc.strftime('%H:%M')} UTC"

        return False, ""

    def assess_risk(
        self,
        signal: TradeSignal,
        tech: TechnicalFeatures,
        account: AccountInfo,
        symbol_cond: SymbolCondition,
        open_positions: List[PositionInfo],
        daily_realized_pnl: float = 0.0,
        upcoming_events: Optional[List[EconomicEvent]] = None,
        current_time_utc: Optional[datetime] = None,
    ) -> RiskAssessment:
        """
        Evaluate full defensive checklist. Returns APPROVED or REJECTED with clear reason.
        """
        now_utc = current_time_utc or datetime.now(timezone.utc)
        events = upcoming_events or []

        # 1. Check Signal validity
        if signal.signal_type not in [SignalType.BUY, SignalType.SELL]:
            return RiskAssessment(
                decision=RiskDecision.REJECTED_INVALID_LOT,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason="Signal is STAND_ASIDE",
                daily_drawdown_pct=0.0,
            )

        # 2. Daily Loss Limit Filter
        # Floating PnL of currently open positions
        floating_pnl = sum(p.profit for p in open_positions)
        total_daily_pnl = daily_realized_pnl + floating_pnl
        daily_loss_usd = max(0.0, -total_daily_pnl)
        daily_dd_pct = (daily_loss_usd / account.balance) if account.balance > 0 else 0.0

        if daily_dd_pct >= self.max_daily_loss_pct:
            reason = f"Daily Loss Limit hit: -{daily_dd_pct:.2%} exceeds max {self.max_daily_loss_pct:.2%}"
            app_logger.warning(f"VETO: {reason}")
            return RiskAssessment(
                decision=RiskDecision.REJECTED_DAILY_LOSS,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason=reason,
                daily_drawdown_pct=daily_dd_pct,
            )

        # 3. Max Concurrent Open Positions
        if len(open_positions) >= self.max_open_positions:
            reason = f"Max open positions reached: {len(open_positions)} >= {self.max_open_positions}"
            app_logger.warning(f"VETO: {reason}")
            return RiskAssessment(
                decision=RiskDecision.REJECTED_MAX_POSITIONS,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason=reason,
                daily_drawdown_pct=daily_dd_pct,
            )

        # 4. Dual Spread Filter
        # 4A. Absolute Spread Check
        if symbol_cond.spread_points > self.max_absolute_spread_pts:
            reason = f"Spread points {symbol_cond.spread_points} exceeds max threshold {self.max_absolute_spread_pts}"
            app_logger.warning(f"VETO: {reason}")
            return RiskAssessment(
                decision=RiskDecision.REJECTED_SPREAD,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason=reason,
                daily_drawdown_pct=daily_dd_pct,
            )

        # 4B. Relative Dynamic Spread Ratio Check
        if tech.spread_ratio > 2.0:
            reason = f"Dynamic spread ratio {tech.spread_ratio:.2f} > 2.0 indicates abnormal market liquidity"
            app_logger.warning(f"VETO: {reason}")
            return RiskAssessment(
                decision=RiskDecision.REJECTED_SPREAD,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason=reason,
                daily_drawdown_pct=daily_dd_pct,
            )

        # 5. Session Guard
        session_blocked, session_reason = self.is_session_guard_active(now_utc)
        if session_blocked:
            app_logger.warning(f"VETO: {session_reason}")
            return RiskAssessment(
                decision=RiskDecision.REJECTED_SESSION_GUARD,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason=session_reason,
                daily_drawdown_pct=daily_dd_pct,
            )

        # 6. News Shield
        news_blocked, news_reason = self.is_news_shield_active(now_utc, events)
        if news_blocked:
            app_logger.warning(f"VETO: {news_reason}")
            return RiskAssessment(
                decision=RiskDecision.REJECTED_NEWS_SHIELD,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason=news_reason,
                daily_drawdown_pct=daily_dd_pct,
            )

        # 7. Calculate Dynamic Lot Size
        lot = self.calculate_lot_size(
            balance=account.balance,
            entry_price=tech.close,
            sl_price=signal.suggested_sl,
            symbol_cond=symbol_cond,
        )

        # 8. Free Margin Check (Require margin to not exceed 30% of Free Margin)
        # Margin for XAUUSD = (Lot * ContractSize * Price) / Leverage
        approx_margin = (lot * symbol_cond.contract_size * tech.close) / max(account.leverage, 1)
        if approx_margin > (account.margin_free * 0.35):
            reason = f"Insufficient Free Margin: Required ~${approx_margin:.2f} exceeds 35% of free margin ${account.margin_free:.2f}"
            app_logger.warning(f"VETO: {reason}")
            return RiskAssessment(
                decision=RiskDecision.REJECTED_MARGIN,
                approved=False,
                calculated_lot=0.0,
                max_risk_usd=0.0,
                reason=reason,
                daily_drawdown_pct=daily_dd_pct,
            )

        max_risk_usd = round(account.balance * self.risk_per_trade_pct, 2)
        app_logger.info(
            f"Risk APPROVED for {signal.signal_type.value}: Lot={lot}, MaxRisk=${max_risk_usd} "
            f"({self.risk_per_trade_pct:.2%}), SL={signal.suggested_sl}, TP={signal.suggested_tp}"
        )

        return RiskAssessment(
            decision=RiskDecision.APPROVED,
            approved=True,
            calculated_lot=lot,
            max_risk_usd=max_risk_usd,
            reason="All defensive risk filters passed successfully.",
            daily_drawdown_pct=daily_dd_pct,
        )
