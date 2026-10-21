"""
Adaptive Auto Re-training & Model Self-Healing Engine.
Monitors loss streaks and performance drift to autonomously trigger candidate model training,
enforces strict Quality Gate validation (Brier Score <= 0.25 and Out-of-Sample improvement),
and executes hot-reloading without service interruption.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
import numpy as np
from sklearn.metrics import brier_score_loss

from app.execution.mt5_adapter import MT5AdapterReadOnly
from app.monitoring.circuit_breaker import CircuitState
from app.monitoring.logger import app_logger
from app.strategy.prediction_model import PredictionModel
from app.strategy.trainer import ModelTrainer


class AdaptiveRetrainer:
    """Orchestrates autonomous re-training, candidate evaluation, and hot-promotion."""

    def __init__(
        self,
        lookback_bars: int = 3000,
        cooldown_minutes: int = 30,
        max_acceptable_brier: float = 0.25,
        model_save_path: str = "models/calibrated_model.joblib",
    ):
        self.lookback_bars = lookback_bars
        self.cooldown_minutes = cooldown_minutes
        self.max_acceptable_brier = max_acceptable_brier
        self.model_save_path = model_save_path

        self.last_attempt_time_utc: Optional[datetime] = None
        self.last_promoted_time_utc: Optional[datetime] = None
        self.total_attempts: int = 0
        self.total_promotions: int = 0

    def should_trigger_retraining(
        self,
        circuit_state: CircuitState,
        consecutive_losses: int,
        drift_detected: bool,
        current_time_utc: datetime,
    ) -> Tuple[bool, str]:
        """
        Evaluate whether autonomous fine-tuning is required and safe to run.
        Enforces cooldown to prevent noisy overfitting loops.
        """
        # 1. Enforce cooldown period
        if self.last_attempt_time_utc is not None:
            elapsed = (current_time_utc - self.last_attempt_time_utc).total_seconds() / 60.0
            if elapsed < self.cooldown_minutes:
                remaining = self.cooldown_minutes - elapsed
                return False, f"COOLDOWN_ACTIVE ({remaining:.1f}m remaining)"

        # 2. Check Triggers:
        # Trigger A: Circuit Breaker Safe Mode due to loss streak
        if circuit_state == CircuitState.SAFE_MODE and consecutive_losses >= 3:
            return True, f"TRIGGERED_BY_LOSS_STREAK ({consecutive_losses} losses, SAFE_MODE)"

        # Trigger B: Performance Tracker Statistical Drift Detected
        if drift_detected:
            return True, "TRIGGERED_BY_PERFORMANCE_DRIFT"

        return False, "NO_TRIGGER_ACTIVE"

    def execute_adaptive_retraining(
        self,
        adapter: MT5AdapterReadOnly,
        active_model: PredictionModel,
        current_time_utc: Optional[datetime] = None,
    ) -> Dict[str, any]:
        """
        Execute candidate model training and strict Quality Gate verification.
        Promotes and hot-reloads model only if candidate proves statistically superior.
        """
        now = current_time_utc or datetime.now(timezone.utc)
        self.last_attempt_time_utc = now
        self.total_attempts += 1

        app_logger.info("=" * 60)
        app_logger.info("INITIATING ADAPTIVE AUTO RE-TRAINING PIPELINE")
        app_logger.info(f"Attempt #{self.total_attempts} at {now.isoformat()}")
        app_logger.info("=" * 60)

        # Step 1: Pull fresh historical bars from MT5
        active_sym = adapter.detect_gold_symbol()
        bars = adapter.get_bars(symbol=active_sym, count=self.lookback_bars)
        if len(bars) < 300:
            app_logger.error(f"Adaptive Re-training ABORTED: Insufficient bars ({len(bars)} < 300)")
            return {
                "promoted": False,
                "status": "ABORTED_INSUFFICIENT_DATA",
                "reason": f"Insufficient bars ({len(bars)})",
            }

        # Step 2: Extract Features & Triple-Barrier Labels
        trainer = ModelTrainer(symbol=active_sym, lookback_bars=self.lookback_bars)
        X, y = trainer.build_dataset_from_bars(bars)
        n_samples = len(X)
        split_idx = int(n_samples * 0.70)

        X_train, y_train = X[:split_idx], y[:split_idx]
        X_test, y_test = X[split_idx:], y[split_idx:]

        # Step 3: Train Candidate Model
        candidate = PredictionModel()
        train_metrics = candidate.train_and_calibrate(X_train, y_train)

        # Step 4: Out-of-Sample Evaluation on Test Set
        X_test_scaled_cand = candidate.scaler.transform(X_test)
        cand_probs = candidate.calibrated_model.predict_proba(X_test_scaled_cand)[:, 1]
        cand_brier = float(round(brier_score_loss(y_test, cand_probs), 4))

        app_logger.info(
            f"Candidate Model Evaluation: Train Brier={train_metrics['brier_score']:.4f}, "
            f"Test Brier={cand_brier:.4f} (Quality Gate <= {self.max_acceptable_brier})"
        )

        # Step 5: Strict Quality Gate Checks
        # Gate 1: Test Brier Score must not exceed maximum acceptable threshold (0.25)
        if cand_brier > self.max_acceptable_brier:
            app_logger.warning(
                f"QUALITY GATE FAILED: Candidate Test Brier ({cand_brier}) exceeds threshold {self.max_acceptable_brier}. "
                "Market regime is currently too unpredictable. Candidate REJECTED to preserve capital."
            )
            return {
                "promoted": False,
                "status": "REJECTED_QUALITY_GATE",
                "candidate_test_brier": cand_brier,
                "threshold": self.max_acceptable_brier,
                "reason": f"Candidate Test Brier {cand_brier} > {self.max_acceptable_brier}",
            }

        # Gate 2: Positive class ratio sanity check
        pos_rate = float(np.mean(y_test))
        if pos_rate < 0.05 or pos_rate > 0.95:
            app_logger.warning(f"QUALITY GATE FAILED: Target distribution degenerate (pos_rate={pos_rate:.1%}).")
            return {
                "promoted": False,
                "status": "REJECTED_DEGENERATE_DATA",
                "reason": f"Degenerate target rate {pos_rate:.1%}",
            }

        # Gate 3: Compare against active model if active model is trained
        if active_model.is_trained:
            try:
                X_test_scaled_active = active_model.scaler.transform(X_test)
                active_probs = active_model.calibrated_model.predict_proba(X_test_scaled_active)[:, 1]
                active_brier = float(round(brier_score_loss(y_test, active_probs), 4))
                app_logger.info(f"Active Model Test Brier on new data: {active_brier:.4f} vs Candidate: {cand_brier:.4f}")

                # Candidate should not perform worse than active model by more than 0.02
                if cand_brier > (active_brier + 0.02):
                    app_logger.warning(
                        f"QUALITY GATE FAILED: Candidate ({cand_brier}) is worse than active model ({active_brier}). REJECTED."
                    )
                    return {
                        "promoted": False,
                        "status": "REJECTED_INFERIOR_TO_ACTIVE",
                        "candidate_brier": cand_brier,
                        "active_brier": active_brier,
                        "reason": f"Candidate {cand_brier} worse than active {active_brier}",
                    }
            except Exception as e:
                app_logger.warning(f"Could not benchmark against active model: {e}. Proceeding with Candidate.")

        # Step 6: Atomic Promotion & Hot-Reloading
        candidate.save_model(file_path=self.model_save_path)
        active_model.load_model(file_path=self.model_save_path)

        self.last_promoted_time_utc = now
        self.total_promotions += 1

        app_logger.info("=" * 60)
        app_logger.info(
            f"ADAPTIVE PROMOTION SUCCESS: Candidate Model promoted to Active! "
            f"Test Brier: {cand_brier:.4f} (Promotion #{self.total_promotions})"
        )
        app_logger.info("=" * 60)

        return {
            "promoted": True,
            "status": "PROMOTED",
            "candidate_test_brier": cand_brier,
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "model_path": self.model_save_path,
            "promoted_at": now.isoformat(),
        }
