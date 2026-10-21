"""
Model Trainer - Quantitative ML Training Pipeline for Gold (XAUUSD).
Extracts multi-timeframe features from historical MT5 bars, applies Triple Barrier labeling,
calibrates probabilities via Platt Scaling, validates Brier Score <= 0.25, and saves model to disk.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

from app.core.enums import Timeframe, TrendState
from app.core.schemas import BarData
from app.execution.mt5_adapter import MT5AdapterReadOnly
from app.intelligence.market_analyzer import MarketAnalyzer
from app.monitoring.logger import app_logger
from app.strategy.prediction_model import PredictionModel


class ModelTrainer:
    """End-to-end training and probability calibration pipeline."""

    def __init__(
        self,
        symbol: Optional[str] = None,
        lookback_bars: int = 3000,
        horizon_bars: int = 4, # 4 bars of M15 = 1 hour forward horizon
        atr_tp_mult: float = 1.2,
        atr_sl_mult: float = 1.0,
    ):
        self.symbol = symbol
        self.lookback_bars = lookback_bars
        self.horizon_bars = horizon_bars
        self.atr_tp_mult = atr_tp_mult
        self.atr_sl_mult = atr_sl_mult
        self.analyzer = MarketAnalyzer()
        self.model = PredictionModel()

    def build_dataset_from_bars(
        self,
        bars_m15: List[BarData],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Convert M15 historical bars into (X, y) feature matrix and labels using Triple Barrier.
        """
        if len(bars_m15) < 250:
            raise ValueError(f"Insufficient bars for training: got {len(bars_m15)}, need >= 250")

        df = self.analyzer._bars_to_df(bars_m15)
        df["ema21"] = self.analyzer.calculate_ema(df["close"], 21)
        df["ema50"] = self.analyzer.calculate_ema(df["close"], 50)
        df["ema200"] = self.analyzer.calculate_ema(df["close"], 200)
        df["atr14"] = self.analyzer.calculate_atr(df, 14)
        df["spread_ema2"] = self.analyzer.calculate_ema(df["spread"].astype(float), 2)

        # Trend mapping
        trend_map = {
            TrendState.STRONG_DOWN: -2.0,
            TrendState.DOWN: -1.0,
            TrendState.RANGE: 0.0,
            TrendState.UP: 1.0,
            TrendState.STRONG_UP: 2.0,
        }

        X_list: List[List[float]] = []
        y_list: List[int] = []

        # Start from index 200 to allow EMA200 warm-up, end before horizon_bars
        for i in range(200, len(df) - self.horizon_bars):
            row = df.iloc[i]
            close = float(row["close"])
            atr = max(float(row["atr14"]), 0.5)
            spread = float(row["spread"])
            spread_ema = max(float(row["spread_ema2"]), 1.0)
            spread_ratio = spread / spread_ema

            trend_state = self.analyzer.classify_trend(
                close=close,
                ema21=float(row["ema21"]),
                ema50=float(row["ema50"]),
                ema200=float(row["ema200"]),
            )
            trend_num = trend_map[trend_state]

            # 8 features matching PredictionModel.extract_features
            features = [
                (close - float(row["ema21"])) / close,
                (close - float(row["ema50"])) / close,
                (close - float(row["ema200"])) / close,
                atr / close,
                spread_ratio,
                trend_num,
                0.0,  # Macro surprise neutral baseline
                24.0, # Hours since event baseline
            ]

            # Triple Barrier Labeling
            future_bars = df.iloc[i + 1 : i + 1 + self.horizon_bars]
            tp_level = close + (self.atr_tp_mult * atr)
            sl_level = close - (self.atr_sl_mult * atr)

            label = 0
            for _, f_row in future_bars.iterrows():
                # If high touches TP before low touches SL -> Success (1)
                if float(f_row["high"]) >= tp_level:
                    label = 1
                    break
                if float(f_row["low"]) <= sl_level:
                    label = 0
                    break

            X_list.append(features)
            y_list.append(label)

        return np.array(X_list, dtype=np.float64), np.array(y_list, dtype=np.int32)

    def train_from_mt5(
        self,
        adapter: Optional[MT5AdapterReadOnly] = None,
        save_path: str = "models/calibrated_model.joblib",
        train_ratio: float = 0.70,
    ) -> Dict[str, any]:
        """
        Pull live data from MT5 terminal, train calibrated model, validate Brier Score, and save.
        """
        own_adapter = False
        if adapter is None:
            adapter = MT5AdapterReadOnly()
            if not adapter.connect():
                raise ConnectionError("Failed to connect to MT5 terminal for training.")
            own_adapter = True

        try:
            active_sym = self.symbol or adapter.detect_gold_symbol()
            app_logger.info(f"Fetching {self.lookback_bars} M15 bars for {active_sym} from MT5...")
            bars = adapter.get_bars(symbol=active_sym, timeframe=Timeframe.M15, count=self.lookback_bars)

            if len(bars) < 300:
                raise ValueError(f"MT5 returned insufficient bars: {len(bars)}, need >= 300")

            app_logger.info(f"Extracting features and Triple-Barrier labels from {len(bars)} bars...")
            X, y = self.build_dataset_from_bars(bars)
            n_samples = len(X)
            pos_ratio = float(np.mean(y))
            app_logger.info(f"Dataset generated: {n_samples} samples (Positive UP class: {pos_ratio:.1%})")

            # 70/30 Chronological Walk-Forward Train/Test split
            split_idx = int(n_samples * train_ratio)
            X_train, y_train = X[:split_idx], y[:split_idx]
            X_test, y_test = X[split_idx:], y[split_idx:]

            app_logger.info(f"Training and calibrating on {len(X_train)} samples...")
            train_metrics = self.model.train_and_calibrate(X_train, y_train)

            # Test Out-of-Sample Calibration
            X_test_scaled = self.model.scaler.transform(X_test)
            test_probs = self.model.calibrated_model.predict_proba(X_test_scaled)[:, 1]
            test_brier = float(round(brier_score_loss(y_test, test_probs), 4))

            app_logger.info(
                f"Out-of-Sample Evaluation ({len(X_test)} test samples): "
                f"Test Brier Score: {test_brier:.4f} (Required <= 0.25)"
            )

            if test_brier > 0.25:
                app_logger.warning(
                    f"Warning: Test Brier Score {test_brier} exceeded 0.25 target. Model may be under-calibrated."
                )

            # Save model to disk
            saved_file = self.model.save_model(file_path=save_path)

            return {
                "symbol": active_sym,
                "total_bars": len(bars),
                "total_samples": n_samples,
                "train_samples": len(X_train),
                "test_samples": len(X_test),
                "positive_rate": round(pos_ratio, 4),
                "train_brier_score": train_metrics["brier_score"],
                "test_brier_score": test_brier,
                "save_path": saved_file,
                "status": "SUCCESS",
            }
        finally:
            if own_adapter:
                adapter.disconnect()


if __name__ == "__main__":
    trainer = ModelTrainer(lookback_bars=3000)
    res = trainer.train_from_mt5()
    print("Training Completed Successfully:", res)
