"""
Baseline Benchmark Strategy - Pure Technical EMA Crossover without AI.
Serves as the benchmark to verify if Machine Learning adds positive economic value.
"""
from datetime import datetime
from typing import List, Optional
import pandas as pd

from app.core.enums import SignalType
from app.core.schemas import TechnicalFeatures, TradeSignal


class BaselineStrategy:
    """Benchmark strategy: EMA 21/50 crossover with EMA 200 trend filter."""

    def __init__(self, atr_sl_mult: float = 1.5, atr_tp_mult: float = 2.0):
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult

    def evaluate(self, tech: TechnicalFeatures, prev_tech: Optional[TechnicalFeatures] = None) -> TradeSignal:
        """
        Evaluate single bar for baseline EMA crossover.
        """
        close = tech.close
        ema21 = tech.ema_21_m15
        ema50 = tech.ema_50_m15
        ema200 = tech.ema_200_m15
        atr = max(tech.atr_14_m15, 1.0)

        # Default: STAND_ASIDE
        signal_type = SignalType.STAND_ASIDE
        reason = "No baseline EMA cross"
        suggested_sl = close
        suggested_tp = close
        win_prob = 0.50

        # Check for crossover if previous bar is provided, or state alignment
        prev_ema21 = prev_tech.ema_21_m15 if prev_tech else ema21
        prev_ema50 = prev_tech.ema_50_m15 if prev_tech else ema50

        # Bullish Crossover in Uptrend
        if (prev_ema21 <= prev_ema50 and ema21 > ema50) and (close > ema200):
            signal_type = SignalType.BUY
            suggested_sl = round(close - (self.atr_sl_mult * atr), 3)
            suggested_tp = round(close + (self.atr_tp_mult * atr), 3)
            win_prob = 0.52
            reason = "Baseline Bullish EMA 21/50 cross above EMA 200"

        # Bearish Crossover in Downtrend
        elif (prev_ema21 >= prev_ema50 and ema21 < ema50) and (close < ema200):
            signal_type = SignalType.SELL
            suggested_sl = round(close + (self.atr_sl_mult * atr), 3)
            suggested_tp = round(close - (self.atr_tp_mult * atr), 3)
            win_prob = 0.52
            reason = "Baseline Bearish EMA 21/50 cross below EMA 200"

        # Rough expected value estimate for baseline
        ev = (win_prob * (self.atr_tp_mult * atr)) - ((1 - win_prob) * (self.atr_sl_mult * atr))

        return TradeSignal(
            symbol=tech.symbol,
            time_utc=tech.time_utc,
            signal_type=signal_type,
            win_probability=win_prob,
            expected_value=round(ev, 2),
            suggested_sl=suggested_sl,
            suggested_tp=suggested_tp,
            reason=reason,
        )
