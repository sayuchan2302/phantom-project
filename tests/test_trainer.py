"""
Unit tests for ML Model Training Pipeline and Model Persistence.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np
import pytest

from app.core.enums import Timeframe
from app.core.schemas import BarData
from app.execution.mt5_adapter import MT5AdapterReadOnly
from app.strategy.prediction_model import PredictionModel
from app.strategy.trainer import ModelTrainer


def make_synthetic_bars(count: int = 400) -> list[BarData]:
    """Generate realistic synthetic Gold M15 bars with upward and downward drift."""
    bars = []
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    price = 2650.0

    for i in range(count):
        # Oscillate price with small random walk
        noise = (i % 7 - 3) * 0.5
        open_p = price + noise
        high_p = open_p + abs(noise) + 1.0
        low_p = open_p - abs(noise) - 1.0
        close_p = open_p + (noise * 0.8)

        bars.append(
            BarData(
                symbol="XAUUSDm",
                timeframe=Timeframe.M15,
                time_utc=base_time + timedelta(minutes=15 * i),
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                tick_volume=150,
                spread=20,
            )
        )
        price = close_p

    return bars


def test_prediction_model_save_and_load(tmp_path: Path):
    """Verify serialization and deserialization of calibrated model & scaler."""
    model = PredictionModel()
    np.random.seed(42)
    X = np.random.randn(60, 8)
    y = np.random.randint(0, 2, size=60)

    # Train
    model.train_and_calibrate(X, y)
    assert model.is_trained is True

    # Save to temp file
    save_file = tmp_path / "test_model.joblib"
    model.save_model(str(save_file))
    assert save_file.exists()

    # Load in new instance
    new_model = PredictionModel()
    assert new_model.is_trained is False
    loaded = new_model.load_model(str(save_file))

    assert loaded is True
    assert new_model.is_trained is True
    assert new_model.last_brier_score == model.last_brier_score

    # Check predictions match exactly
    prob_up1, prob_down1 = model.predict_probability(None if hasattr(model, '_raw') else MagicMock(technical=MagicMock(close=2650, ema_21_m15=2648, ema_50_m15=2645, ema_200_m15=2630, atr_14_m15=3.0, current_spread=20, spread_ratio=1.0, trend_state=MagicMock(value="UP")), latest_news=None, minutes_since_last_event=None))
    prob_up2, prob_down2 = new_model.predict_probability(None if hasattr(new_model, '_raw') else MagicMock(technical=MagicMock(close=2650, ema_21_m15=2648, ema_50_m15=2645, ema_200_m15=2630, atr_14_m15=3.0, current_spread=20, spread_ratio=1.0, trend_state=MagicMock(value="UP")), latest_news=None, minutes_since_last_event=None))

    assert prob_up1 == prob_up2
    assert prob_down1 == prob_down2


def test_trainer_build_dataset_and_triple_barrier():
    """Verify feature matrix extraction and Triple Barrier labels."""
    bars = make_synthetic_bars(350)
    trainer = ModelTrainer(lookback_bars=350, horizon_bars=4)

    X, y = trainer.build_dataset_from_bars(bars)

    # Expect: 350 - 200 (warmup) - 4 (horizon) = 146 samples
    assert len(X) == 146
    assert len(y) == 146
    assert X.shape[1] == 8
    assert set(np.unique(y)).issubset({0, 1})


def test_trainer_mock_mt5_execution(tmp_path: Path):
    """Test full training pipeline with mock MT5 adapter."""
    mock_adapter = MagicMock(spec=MT5AdapterReadOnly)
    mock_adapter.detect_gold_symbol.return_value = "XAUUSDm"
    mock_adapter.get_bars.return_value = make_synthetic_bars(450)

    save_path = str(tmp_path / "models" / "mock_calibrated_model.joblib")
    trainer = ModelTrainer(lookback_bars=450)

    res = trainer.train_from_mt5(adapter=mock_adapter, save_path=save_path)

    assert res["status"] == "SUCCESS"
    assert res["test_brier_score"] <= 0.25
    assert Path(save_path).exists()
    assert res["total_samples"] > 100
