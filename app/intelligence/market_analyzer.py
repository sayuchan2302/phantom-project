"""
Market Analyzer - Technical Analysis & Feature Engineering.
Calculates multi-timeframe EMAs, ATR, Dynamic Spread Ratio, and Trend State.
"""
from datetime import datetime, timezone
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd

from app.core.enums import Timeframe, TrendState
from app.core.schemas import BarData, SymbolCondition, TechnicalFeatures
from app.monitoring.logger import app_logger


class MarketAnalyzer:
    """Computes technical indicators and features from raw bar and spread data."""

    @staticmethod
    def _bars_to_df(bars: List[BarData]) -> pd.DataFrame:
        """Convert list of BarData to sorted Pandas DataFrame."""
        if not bars:
            return pd.DataFrame()

        data = [
            {
                "time_utc": b.time_utc,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "spread": b.spread,
                "tick_volume": b.tick_volume,
            }
            for b in bars
        ]
        df = pd.DataFrame(data).sort_values("time_utc").reset_index(drop=True)
        return df

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """Compute Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range (ATR)."""
        if len(df) < period + 1:
            return pd.Series([0.0] * len(df))

        high_low = df["high"] - df["low"]
        high_prev_close = (df["high"] - df["close"].shift(1)).abs()
        low_prev_close = (df["low"] - df["close"].shift(1)).abs()

        tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        return atr

    def classify_trend(self, close: float, ema21: float, ema50: float, ema200: float) -> TrendState:
        """
        Classify trend state based on price and EMA 21/50/200 alignment.
        """
        if close > ema21 > ema50 > ema200:
            return TrendState.STRONG_UP
        elif close > ema50 > ema200:
            return TrendState.UP
        elif close < ema21 < ema50 < ema200:
            return TrendState.STRONG_DOWN
        elif close < ema50 < ema200:
            return TrendState.DOWN
        else:
            return TrendState.RANGE

    def analyze(
        self,
        symbol: str,
        bars_m15: List[BarData],
        bars_h1: List[BarData],
        current_condition: SymbolCondition,
    ) -> TechnicalFeatures:
        """
        Calculate full set of technical features across M15 and H1.
        """
        if len(bars_m15) < 30:
            raise ValueError(f"Insufficient M15 bars for analysis: got {len(bars_m15)}, need >= 30")

        df_m15 = self._bars_to_df(bars_m15)
        df_h1 = self._bars_to_df(bars_h1) if bars_h1 else df_m15

        # M15 EMAs
        df_m15["ema21"] = self.calculate_ema(df_m15["close"], 21)
        df_m15["ema50"] = self.calculate_ema(df_m15["close"], 50)
        df_m15["ema200"] = self.calculate_ema(df_m15["close"], 200)

        # M15 ATR
        df_m15["atr14"] = self.calculate_atr(df_m15, 14)

        # Dynamic Spread Ratio: current_spread / EMA_30min(spread)
        # On M15, 30 min is 2 bars
        df_m15["spread_ema2"] = self.calculate_ema(df_m15["spread"].astype(float), 2)
        spread_ema_30m = float(df_m15["spread_ema2"].iloc[-1])
        current_spread = current_condition.spread_points

        # Avoid zero division
        if spread_ema_30m <= 0:
            spread_ema_30m = max(float(current_spread), 1.0)
        spread_ratio = round(current_spread / spread_ema_30m, 2)

        # H1 EMAs
        if len(df_h1) >= 20:
            df_h1["ema21"] = self.calculate_ema(df_h1["close"], 21)
            df_h1["ema50"] = self.calculate_ema(df_h1["close"], 50)
            df_h1["ema200"] = self.calculate_ema(df_h1["close"], 200)
            ema21_h1 = float(df_h1["ema21"].iloc[-1])
            ema50_h1 = float(df_h1["ema50"].iloc[-1])
            ema200_h1 = float(df_h1["ema200"].iloc[-1])
        else:
            # Fallback to M15 if H1 has few bars
            ema21_h1 = float(df_m15["ema21"].iloc[-1])
            ema50_h1 = float(df_m15["ema50"].iloc[-1])
            ema200_h1 = float(df_m15["ema200"].iloc[-1])

        latest_close = float(df_m15["close"].iloc[-1])
        ema21_m15 = float(df_m15["ema21"].iloc[-1])
        ema50_m15 = float(df_m15["ema50"].iloc[-1])
        ema200_m15 = float(df_m15["ema200"].iloc[-1])
        atr14_m15 = float(df_m15["atr14"].iloc[-1])

        trend_state = self.classify_trend(latest_close, ema21_m15, ema50_m15, ema200_m15)

        app_logger.debug(
            f"Market Analysis [{symbol}]: Close={latest_close}, Trend={trend_state.value}, "
            f"ATR(14)={atr14_m15:.2f}, SpreadRatio={spread_ratio} (Curr: {current_spread}, EMA: {spread_ema_30m:.1f})"
        )

        return TechnicalFeatures(
            symbol=symbol,
            time_utc=df_m15["time_utc"].iloc[-1],
            close=latest_close,
            ema_21_m15=round(ema21_m15, 3),
            ema_50_m15=round(ema50_m15, 3),
            ema_200_m15=round(ema200_m15, 3),
            ema_21_h1=round(ema21_h1, 3),
            ema_50_h1=round(ema50_m15, 3),
            ema_200_h1=round(ema200_h1, 3),
            atr_14_m15=round(atr14_m15, 3),
            current_spread=current_spread,
            spread_ema_30m=round(spread_ema_30m, 1),
            spread_ratio=spread_ratio,
            trend_state=trend_state,
        )
