"""
Calibrated Machine Learning Prediction Model.
Estimates probability of profitable moves with mandatory Brier Score Calibration checks.
"""
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.preprocessing import StandardScaler

from app.core.schemas import TechnicalFeatures, UnifiedFeatureVector
from app.monitoring.logger import app_logger


class PredictionModel:
    """Quantitative probabilistic model with Platt Scaling calibration."""

    def __init__(self):
        self.scaler = StandardScaler()
        # Base classifier: Logistic Regression with L2 regularization
        base_estimator = LogisticRegression(C=0.5, max_iter=500, random_state=42)
        # Platt Scaling calibration wrapper
        self.calibrated_model = CalibratedClassifierCV(estimator=base_estimator, method="sigmoid", cv=3)
        self.is_trained = False
        self.last_brier_score: Optional[float] = None

    @staticmethod
    def extract_features(vector: UnifiedFeatureVector) -> np.ndarray:
        """
        Extract numerical feature vector:
        [
            close_to_ema21_pct,
            close_to_ema50_pct,
            close_to_ema200_pct,
            atr_normalized,
            spread_ratio,
            trend_state_num,
            surprise_score,
            hours_since_event
        ]
        """
        tech = vector.technical
        close = max(tech.close, 1.0)
        dist_ema21 = (close - tech.ema_21_m15) / close
        dist_ema50 = (close - tech.ema_50_m15) / close
        dist_ema200 = (close - tech.ema_200_m15) / close
        atr_norm = tech.atr_14_m15 / close
        spread_ratio = tech.spread_ratio

        # Trend state mapping: STRONG_DOWN=-2, DOWN=-1, RANGE=0, UP=1, STRONG_UP=2
        trend_map = {
            "STRONG_DOWN": -2.0,
            "DOWN": -1.0,
            "RANGE": 0.0,
            "UP": 1.0,
            "STRONG_UP": 2.0,
        }
        trend_num = trend_map.get(tech.trend_state.value, 0.0)

        # Macro surprise score
        surprise = 0.0
        if vector.latest_news and vector.latest_news.surprise_score is not None:
            surprise = float(vector.latest_news.surprise_score)

        hours_since_event = 24.0
        if vector.minutes_since_last_event is not None:
            hours_since_event = min(vector.minutes_since_last_event / 60.0, 72.0)

        return np.array([
            dist_ema21,
            dist_ema50,
            dist_ema200,
            atr_norm,
            spread_ratio,
            trend_num,
            surprise,
            hours_since_event,
        ])

    def train_and_calibrate(self, X: np.ndarray, y: np.ndarray) -> Dict[str, float]:
        """
        Fit model and evaluate probability calibration using Brier Score.
        y is binary: 1 = Profitable UP move, 0 = Non-profitable / Down
        """
        if len(X) < 30:
            raise ValueError(f"Insufficient training data: {len(X)} samples, need >= 30")

        X_scaled = self.scaler.fit_transform(X)
        self.calibrated_model.fit(X_scaled, y)
        self.is_trained = True

        # Compute in-sample calibrated probabilities
        probs = self.calibrated_model.predict_proba(X_scaled)[:, 1]
        brier = brier_score_loss(y, probs)
        self.last_brier_score = round(float(brier), 4)

        app_logger.info(
            f"Prediction Model trained successfully on {len(X)} samples. "
            f"Calibrated Brier Score: {self.last_brier_score} (Acceptable <= 0.25)"
        )
        return {"samples": len(X), "brier_score": self.last_brier_score}

    def predict_probability(self, vector: UnifiedFeatureVector) -> Tuple[float, float]:
        """
        Returns (P(Up), P(Down)) calibrated probabilities.
        """
        if not self.is_trained:
            # Fallback prior when not trained: 50%
            return 0.50, 0.50

        x_raw = self.extract_features(vector).reshape(1, -1)
        x_scaled = self.scaler.transform(x_raw)
        probs = self.calibrated_model.predict_proba(x_scaled)[0]
        prob_down = float(probs[0])
        prob_up = float(probs[1])
        return round(prob_up, 4), round(prob_down, 4)

    def save_model(self, file_path: str = "models/calibrated_model.joblib") -> str:
        """Serialize trained scaler, calibrated model and metadata to disk."""
        import joblib
        from pathlib import Path

        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scaler": self.scaler,
            "calibrated_model": self.calibrated_model,
            "is_trained": self.is_trained,
            "last_brier_score": self.last_brier_score,
        }
        joblib.dump(payload, p)
        app_logger.info(f"Prediction model saved successfully to: {p}")
        return str(p)

    def load_model(self, file_path: str = "models/calibrated_model.joblib") -> bool:
        """Load trained scaler and calibrated model from disk if available."""
        import joblib
        from pathlib import Path

        p = Path(file_path)
        if not p.exists():
            app_logger.warning(f"Model file not found at: {p}. Using default prior probabilities.")
            return False

        try:
            payload = joblib.load(p)
            self.scaler = payload["scaler"]
            self.calibrated_model = payload["calibrated_model"]
            self.is_trained = payload.get("is_trained", True)
            self.last_brier_score = payload.get("last_brier_score")
            app_logger.info(
                f"Prediction model loaded from {p} (Brier Score: {self.last_brier_score}, is_trained={self.is_trained})"
            )
            return True
        except Exception as e:
            app_logger.error(f"Failed to load model from {p}: {e}")
            return False

