"""
Signal Engine - Expected Value Decision Core.
Synthesizes calibrated probability, risk-reward parameters, and execution friction
to issue BUY, SELL, or STAND_ASIDE signals strictly when EV > 0.
"""
from datetime import datetime
from typing import Optional
from app.core.enums import SignalType, TrendState
from app.core.schemas import TradeSignal, UnifiedFeatureVector
from app.monitoring.logger import app_logger
from app.strategy.prediction_model import PredictionModel


class SignalEngine:
    """Combines statistical probabilities and expected value calculation to emit trade signals."""

    def __init__(
        self,
        model: PredictionModel,
        min_win_prob: float = 0.54,
        atr_sl_mult: float = 1.5,
        atr_tp_mult: float = 2.2,
        estimated_slippage_pips: float = 1.0,
        commission_per_lot_usd: float = 3.5,
    ):
        self.model = model
        self.min_win_prob = min_win_prob
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult
        self.estimated_slippage_pips = estimated_slippage_pips
        self.commission_per_lot_usd = commission_per_lot_usd

    def calculate_expected_value(
        self,
        win_prob: float,
        reward_usd: float,
        risk_usd: float,
        spread_usd: float,
        slippage_usd: float,
        commission_usd: float,
    ) -> float:
        """
        EV = P(win) * Reward - P(loss) * Risk - Total Costs
        """
        loss_prob = 1.0 - win_prob
        gross_ev = (win_prob * reward_usd) - (loss_prob * risk_usd)
        total_friction = spread_usd + slippage_usd + commission_usd
        net_ev = gross_ev - total_friction
        return round(net_ev, 2)

    def evaluate_opportunity(
        self,
        vector: UnifiedFeatureVector,
        lot_size: float = 0.1,
    ) -> TradeSignal:
        """
        Generate trade signal based on calibrated probability and positive EV check.
        """
        tech = vector.technical
        close = tech.close
        atr = max(tech.atr_14_m15, 1.0)
        symbol = tech.symbol

        # Filter out abnormally wide spread (> 2.0x 30min EMA)
        if tech.spread_ratio > 2.0:
            return TradeSignal(
                symbol=symbol,
                time_utc=tech.time_utc,
                signal_type=SignalType.STAND_ASIDE,
                win_probability=0.0,
                expected_value=0.0,
                suggested_sl=close,
                suggested_tp=close,
                reason=f"STAND_ASIDE: Spread is abnormally wide (ratio={tech.spread_ratio:.2f} > 2.0)",
            )

        # Get calibrated probabilities from ML model
        p_up, p_down = self.model.predict_probability(vector)

        # Approximate cost components for 0.1 lot of XAUUSD (10 oz)
        # 1 point in XAUUSD = $0.01 per 0.1 lot ($0.1 per point for 1.0 lot)
        spread_cost = (tech.current_spread * 0.001 * 100.0) * lot_size
        slippage_cost = (self.estimated_slippage_pips * 0.1 * 100.0) * lot_size
        commission_cost = self.commission_per_lot_usd * lot_size

        risk_distance = self.atr_sl_mult * atr
        reward_distance = self.atr_tp_mult * atr
        risk_usd = risk_distance * 100.0 * lot_size
        reward_usd = reward_distance * 100.0 * lot_size

        # Check BUY
        if p_up >= self.min_win_prob and tech.trend_state in [TrendState.UP, TrendState.STRONG_UP, TrendState.RANGE]:
            ev = self.calculate_expected_value(
                win_prob=p_up,
                reward_usd=reward_usd,
                risk_usd=risk_usd,
                spread_usd=spread_cost,
                slippage_usd=slippage_cost,
                commission_usd=commission_cost,
            )
            if ev > 0:
                suggested_sl = round(close - risk_distance, 3)
                suggested_tp = round(close + reward_distance, 3)
                return TradeSignal(
                    symbol=symbol,
                    time_utc=tech.time_utc,
                    signal_type=SignalType.BUY,
                    win_probability=p_up,
                    expected_value=ev,
                    suggested_sl=suggested_sl,
                    suggested_tp=suggested_tp,
                    reason=f"BUY Signal: Calibrated P(Up)={p_up:.1%}, Net EV=+${ev:.2f} (Reward ${reward_usd:.1f} / Risk ${risk_usd:.1f})",
                )

        # Check SELL
        if p_down >= self.min_win_prob and tech.trend_state in [TrendState.DOWN, TrendState.STRONG_DOWN, TrendState.RANGE]:
            ev = self.calculate_expected_value(
                win_prob=p_down,
                reward_usd=reward_usd,
                risk_usd=risk_usd,
                spread_usd=spread_cost,
                slippage_usd=slippage_cost,
                commission_usd=commission_cost,
            )
            if ev > 0:
                suggested_sl = round(close + risk_distance, 3)
                suggested_tp = round(close - reward_distance, 3)
                return TradeSignal(
                    symbol=symbol,
                    time_utc=tech.time_utc,
                    signal_type=SignalType.SELL,
                    win_probability=p_down,
                    expected_value=ev,
                    suggested_sl=suggested_sl,
                    suggested_tp=suggested_tp,
                    reason=f"SELL Signal: Calibrated P(Down)={p_down:.1%}, Net EV=+${ev:.2f} (Reward ${reward_usd:.1f} / Risk ${risk_usd:.1f})",
                )

        return TradeSignal(
            symbol=symbol,
            time_utc=tech.time_utc,
            signal_type=SignalType.STAND_ASIDE,
            win_probability=max(p_up, p_down),
            expected_value=0.0,
            suggested_sl=close,
            suggested_tp=close,
            reason=f"STAND_ASIDE: EV <= 0 or insufficient edge (P(Up)={p_up:.1%}, P(Down)={p_down:.1%})",
        )
