"""
Unit Tests for Adaptive Auto Re-training & Self-Healing Engine.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np
import pytest

from app.core.enums import Timeframe
from app.core.schemas import BarData
from app.execution.mt5_adapter import MT5AdapterReadOnly
from app.monitoring.circuit_breaker import CircuitState
from app.strategy.adaptive_manager import AdaptiveRetrainer
from app.strategy.prediction_model import PredictionModel


def make_synthetic_bars(count: int = 400) -> list[BarData]:
    """Generate realistic synthetic Gold M15 bars."""
    bars = []
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    price = 2650.0

    for i in range(count):
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


def test_adaptive_retraining_trigger_conditions():
    retrainer = AdaptiveRetrainer(cooldown_minutes=30)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    # 1. Normal state -> No trigger
    should, reason = retrainer.should_trigger_retraining(
        circuit_state=CircuitState.NORMAL,
        consecutive_losses=0,
        drift_detected=False,
        current_time_utc=now,
    )
    assert should is False
    assert reason == "NO_TRIGGER_ACTIVE"

    # 2. Safe Mode with 4 losses -> Triggered
    should, reason = retrainer.should_trigger_retraining(
        circuit_state=CircuitState.SAFE_MODE,
        consecutive_losses=4,
        drift_detected=False,
        current_time_utc=now,
    )
    assert should is True
    assert "TRIGGERED_BY_LOSS_STREAK" in reason

    # 3. Simulate training run at time `now`
    retrainer.last_attempt_time_utc = now

    # 4. Attempt again 10 minutes later -> Cooldown active!
    later_10m = now + timedelta(minutes=10)
    should, reason = retrainer.should_trigger_retraining(
        circuit_state=CircuitState.SAFE_MODE,
        consecutive_losses=4,
        drift_detected=False,
        current_time_utc=later_10m,
    )
    assert should is False
    assert "COOLDOWN_ACTIVE" in reason

    # 5. Attempt 35 minutes later with drift -> Triggered
    later_35m = now + timedelta(minutes=35)
    should, reason = retrainer.should_trigger_retraining(
        circuit_state=CircuitState.NORMAL,
        consecutive_losses=1,
        drift_detected=True,
        current_time_utc=later_35m,
    )
    assert should is True
    assert "TRIGGERED_BY_PERFORMANCE_DRIFT" in reason


def test_adaptive_retraining_promotion_success(tmp_path: Path):
    mock_adapter = MagicMock(spec=MT5AdapterReadOnly)
    mock_adapter.detect_gold_symbol.return_value = "XAUUSDm"
    mock_adapter.get_bars.return_value = make_synthetic_bars(450)

    save_file = str(tmp_path / "models" / "adaptive_model.joblib")
    retrainer = AdaptiveRetrainer(lookback_bars=450, max_acceptable_brier=0.25, model_save_path=save_file)
    active_model = PredictionModel()

    res = retrainer.execute_adaptive_retraining(adapter=mock_adapter, active_model=active_model)

    assert res["promoted"] is True
    assert res["status"] == "PROMOTED"
    assert res["candidate_test_brier"] <= 0.25
    assert active_model.is_trained is True
    assert Path(save_file).exists()


def test_adaptive_retraining_rejection_quality_gate(tmp_path: Path):
    mock_adapter = MagicMock(spec=MT5AdapterReadOnly)
    mock_adapter.detect_gold_symbol.return_value = "XAUUSDm"
    mock_adapter.get_bars.return_value = make_synthetic_bars(450)

    save_file = str(tmp_path / "models" / "adaptive_model.joblib")
    # Set threshold of 0.001 (< 0.0046 synthetic brier) -> Must be rejected!
    retrainer = AdaptiveRetrainer(lookback_bars=450, max_acceptable_brier=0.001, model_save_path=save_file)
    active_model = PredictionModel()

    res = retrainer.execute_adaptive_retraining(adapter=mock_adapter, active_model=active_model)

    assert res["promoted"] is False
    assert res["status"] == "REJECTED_QUALITY_GATE"
    assert active_model.is_trained is False # Was not promoted
