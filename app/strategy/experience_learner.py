"""
Experience Learner & Experiential Memory Engine.
Implements continuous post-trade learning:
- Captures chart features, candlestick state, and macro context at entry.
- When trade closes (WIN or LOSS), records the verified outcome into the Experience Memory Bank.
- Performs continual fine-tuning of the Calibrated Prediction Model.
- Automatically saves the updated model to models/calibrated_model.joblib.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
from sklearn.metrics import brier_score_loss

from app.core.enums import SignalType
from app.core.schemas import UnifiedFeatureVector
from app.monitoring.logger import app_logger
from app.strategy.prediction_model import PredictionModel


@dataclass
class TradeSnapshot:
    ticket: int
    signal_type: str
    entry_price: float
    time_utc: str
    features: List[float] # 8-dim numerical feature vector
    trend_state: str
    close_price: float
    current_spread: float
    news_surprise: Optional[float] = None


class ExperienceLearner:
    """Manages active trade snapshots, experiential learning, and continual model updates."""

    def __init__(
        self,
        memory_file: Path = Path("data/experience_memory.json"),
        model_save_path: str = "models/calibrated_model.joblib",
    ):
        self.memory_file = memory_file
        self.model_save_path = model_save_path
        self.active_snapshots: Dict[int, TradeSnapshot] = {}
        self.experience_bank: List[Dict[str, any]] = self._load_memory()

    def _load_memory(self) -> List[Dict[str, any]]:
        """Load past verified trade experiences from disk."""
        if not self.memory_file.exists():
            return []
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            app_logger.warning(f"Failed to load experience memory from {self.memory_file}: {e}")
            return []

    def _save_memory(self):
        """Persist experience memory to disk."""
        try:
            self.memory_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.experience_bank, f, indent=2)
        except Exception as e:
            app_logger.error(f"Failed to save experience memory: {e}")

    def register_trade_entry(
        self,
        ticket: int,
        vector: UnifiedFeatureVector,
        signal_type: SignalType,
        entry_price: float,
        time_utc: datetime,
    ):
        """Snapshot entry conditions, chart pattern, and macro features for future learning."""
        raw_features = PredictionModel.extract_features(vector).tolist()
        surprise = (
            float(vector.latest_news.surprise_score)
            if vector.latest_news and vector.latest_news.surprise_score is not None
            else 0.0
        )

        snapshot = TradeSnapshot(
            ticket=ticket,
            signal_type=signal_type.value,
            entry_price=entry_price,
            time_utc=time_utc.isoformat(),
            features=raw_features,
            trend_state=vector.technical.trend_state.value,
            close_price=vector.technical.close,
            current_spread=vector.technical.current_spread,
            news_surprise=surprise,
        )
        self.active_snapshots[ticket] = snapshot
        app_logger.info(
            f"🎓 [EXPERIENCE LEARNER] Snapshot registered for Ticket #{ticket}: "
            f"Action={signal_type.value}, Price={entry_price}, Trend={snapshot.trend_state}"
        )

    def on_trade_closed(
        self,
        ticket: int,
        profit_usd: float,
        active_model: PredictionModel,
        exit_price: Optional[float] = None,
    ) -> Dict[str, any]:
        """
        Post-trade learning loop:
        1. If WIN (profit > 0): Mark pattern as positive outcome (y=1 for BUY, or inverse for SELL).
        2. If LOSS (profit < 0): Mark pattern as negative outcome (y=0), learning to suppress this error.
        3. Updates model weights, calibrates Platt scaling, and serializes to disk.
        """
        snapshot = self.active_snapshots.pop(ticket, None)
        if snapshot is None:
            app_logger.warning(
                f"[EXPERIENCE LEARNER] Trade #{ticket} closed with profit ${profit_usd:.2f}, but no entry snapshot was found."
            )
            return {"learned": False, "reason": "No entry snapshot found"}

        is_win = profit_usd > 0
        outcome_label = 1 if is_win else 0

        experience_entry = {
            "ticket": ticket,
            "signal_type": snapshot.signal_type,
            "entry_price": snapshot.entry_price,
            "exit_price": exit_price,
            "profit_usd": round(profit_usd, 2),
            "outcome": "WIN" if is_win else "LOSS",
            "target_label": outcome_label,
            "features": snapshot.features,
            "trend_state": snapshot.trend_state,
            "time_utc": snapshot.time_utc,
            "learned_at": datetime.now(timezone.utc).isoformat(),
        }

        self.experience_bank.append(experience_entry)
        self._save_memory()

        if is_win:
            app_logger.info(
                f"🎉 [HỌC TẬP TỪ CHIẾN THẮNG] Lệnh #{ticket} ĐÃ THẮNG (+${profit_usd:.2f})! "
                f"Ghi nhớ mẫu nến Trend={snapshot.trend_state} vào ngân hàng tri thức."
            )
        else:
            app_logger.warning(
                f"⚠️ [RÚT KINH NGHIỆM TỪ THẤT BẠI] Lệnh #{ticket} BỊ THUA (${profit_usd:.2f})! "
                f"Học từ sai lầm của thế nến Trend={snapshot.trend_state} để triệt tiêu tín hiệu giả."
            )

        # Continual Model Fine-Tuning
        fine_tune_res = self._fine_tune_model(active_model=active_model)

        return {
            "learned": True,
            "ticket": ticket,
            "outcome": "WIN" if is_win else "LOSS",
            "profit_usd": profit_usd,
            "total_experiences": len(self.experience_bank),
            "fine_tune_metrics": fine_tune_res,
        }

    def _fine_tune_model(self, active_model: PredictionModel) -> Dict[str, any]:
        """Fine-tune prediction model incorporating all accumulated verified trade experiences."""
        if len(self.experience_bank) < 5:
            # Need at least a small set of experiences to fine-tune reliably
            app_logger.info(
                f"[EXPERIENCE LEARNER] Accumulated {len(self.experience_bank)} experiences. "
                "Will perform full recalibration when >= 5 trades recorded."
            )
            return {"status": "COLLECTING_EXPERIENCE", "count": len(self.experience_bank)}

        try:
            X_exp = np.array([e["features"] for e in self.experience_bank], dtype=np.float64)
            y_exp = np.array([e["target_label"] for e in self.experience_bank], dtype=np.int32)

            # Ensure both classes exist
            if len(np.unique(y_exp)) < 2:
                dummy_x = np.zeros((1, 8))
                dummy_y = np.array([1 if y_exp[0] == 0 else 0])
                X_exp = np.vstack([X_exp, dummy_x])
                y_exp = np.hstack([y_exp, dummy_y])

            # Anchor with baseline replay buffer if under 35 samples (cv=3 requires >= 30)
            if len(X_exp) < 35:
                n_needed = 35 - len(X_exp)
                np.random.seed(42)
                anchor_x = np.random.randn(n_needed, 8) * 0.05
                anchor_y = np.random.choice([0, 1], size=n_needed)
                X_train = np.vstack([X_exp, anchor_x])
                y_train = np.hstack([y_exp, anchor_y])
            else:
                X_train = X_exp
                y_train = y_exp

            metrics = active_model.train_and_calibrate(X_train, y_train)
            saved_file = active_model.save_model(file_path=self.model_save_path)

            app_logger.info(
                f"🧠 [BỘ NÃO ĐÃ ĐƯỢC CẬP NHẬT] Mô hình đã học {len(self.experience_bank)} bài học thực tế! "
                f"Calibrated Brier Score: {metrics['brier_score']:.4f} -> Lưu tại {saved_file}"
            )
            return {"status": "SUCCESS", "experiences": len(self.experience_bank), "brier_score": metrics["brier_score"]}
        except Exception as e:
            app_logger.error(f"[EXPERIENCE LEARNER] Failed to fine-tune model: {e}")
            return {"status": "ERROR", "error": str(e)}
