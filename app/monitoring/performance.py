"""
Performance Tracker & Drift Detection Engine.
Monitors execution slippage in real-time and detects model performance degradation
by comparing live demo metrics against backtest assumptions.
"""
from typing import Dict, List, Optional
import numpy as np

from app.monitoring.logger import app_logger


class PerformanceTracker:
    """Tracks live slippage, execution quality, and performance drift."""

    def __init__(self, backtest_assumed_slippage_pips: float = 0.8, backtest_winrate: float = 40.0):
        self.backtest_slippage_pips = backtest_assumed_slippage_pips
        self.backtest_winrate = backtest_winrate
        self.slippage_records: List[float] = [] # in pips
        self.trades_pnl: List[float] = []

    def record_execution(self, requested_price: float, filled_price: float, point_value: float = 0.001) -> float:
        """
        Record slippage for a single execution:
        Slippage (pips) = |filled - requested| / (point * 10)
        """
        price_diff = abs(filled_price - requested_price)
        slippage_pips = round(price_diff / (point_value * 10.0), 2)
        self.slippage_records.append(slippage_pips)

        app_logger.info(
            f"Slippage Recorded: Requested={requested_price}, Filled={filled_price} -> "
            f"Slippage={slippage_pips} pips (Backtest assumed: {self.backtest_slippage_pips} pips)"
        )
        return slippage_pips

    def record_closed_trade(self, net_pnl: float):
        """Record completed trade PnL."""
        self.trades_pnl.append(net_pnl)

    def get_slippage_stats(self) -> Dict[str, float]:
        """Compute summary statistics for slippage."""
        if not self.slippage_records:
            return {"count": 0, "avg_slippage_pips": 0.0, "max_slippage_pips": 0.0}

        return {
            "count": len(self.slippage_records),
            "avg_slippage_pips": round(float(np.mean(self.slippage_records)), 2),
            "max_slippage_pips": round(float(np.max(self.slippage_records)), 2),
            "backtest_benchmark_pips": self.backtest_slippage_pips,
        }

    def detect_performance_drift(self) -> Dict[str, any]:
        """
        Check if live performance has drifted excessively from backtest assumptions.
        Alerts if live winrate drops by more than 20% compared to backtest.
        """
        if len(self.trades_pnl) < 5:
            return {
                "drift_detected": False,
                "reason": "Insufficient live trades (< 5) to evaluate drift",
                "live_winrate": 0.0,
            }

        wins = [p for p in self.trades_pnl if p > 0]
        live_winrate = (len(wins) / len(self.trades_pnl)) * 100.0
        winrate_diff = self.backtest_winrate - live_winrate

        # If live winrate is > 20% lower than backtest, flag drift
        drift_detected = winrate_diff > 20.0
        reason = (
            f"Live WinRate ({live_winrate:.1f}%) is {winrate_diff:.1f}% below backtest ({self.backtest_winrate:.1f}%)"
            if drift_detected
            else "Performance within expected statistical tolerance"
        )

        if drift_detected:
            app_logger.warning(f"DRIFT DETECTED: {reason}. Recommendation: Review model calibration.")

        return {
            "drift_detected": drift_detected,
            "live_winrate": round(live_winrate, 1),
            "backtest_winrate": self.backtest_winrate,
            "winrate_difference": round(winrate_diff, 1),
            "reason": reason,
        }
