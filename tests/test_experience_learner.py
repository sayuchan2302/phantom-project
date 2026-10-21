"""
Unit tests for ExperienceLearner - Post-Trade Learning & Memory Persistence.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np
import pytest

from app.core.enums import SignalType, TrendState
from app.core.schemas import TechnicalFeatures, UnifiedFeatureVector
from app.strategy.experience_learner import ExperienceLearner
from app.strategy.prediction_model import PredictionModel


def make_dummy_vector() -> UnifiedFeatureVector:
    tech = TechnicalFeatures(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        close=2650.0,
        ema_21_m15=2648.0,
        ema_50_m15=2645.0,
        ema_200_m15=2630.0,
        ema_21_h1=2648.0,
        ema_50_h1=2645.0,
        ema_200_h1=2630.0,
        atr_14_m15=3.0,
        current_spread=20,
        spread_ema_30m=20.0,
        spread_ratio=1.0,
        trend_state=TrendState.UP,
    )
    now = datetime.now(timezone.utc)
    return UnifiedFeatureVector(
        symbol="XAUUSDm",
        time_utc=now,
        technical=tech,
        latest_news=None,
        minutes_since_last_event=None,
        calculation_latency_ms=10.0,
    )


def test_experience_learner_lifecycle(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    model_file = str(tmp_path / "calibrated_model.joblib")
    learner = ExperienceLearner(memory_file=mem_file, model_save_path=model_file)
    active_model = PredictionModel()

    # Pre-train active model so it's initialized
    X_init = np.random.randn(40, 8)
    y_init = np.random.randint(0, 2, size=40)
    active_model.train_and_calibrate(X_init, y_init)

    vec = make_dummy_vector()
    ticket = 1001

    # 1. Register trade entry
    learner.register_trade_entry(
        ticket=ticket,
        vector=vec,
        signal_type=SignalType.BUY,
        entry_price=2650.0,
        time_utc=datetime.now(timezone.utc),
    )
    assert ticket in learner.active_snapshots

    # 2. Simulate WIN trade closing with +$2.15 profit
    res = learner.on_trade_closed(ticket=ticket, profit_usd=2.15, active_model=active_model)
    assert res["learned"] is True
    assert res["outcome"] == "WIN"
    assert res["total_experiences"] == 1
    assert mem_file.exists()

    # 3. Register second trade & simulate LOSS trade with -$1.90
    ticket_2 = 1002
    learner.register_trade_entry(
        ticket=ticket_2,
        vector=vec,
        signal_type=SignalType.SELL,
        entry_price=2648.0,
        time_utc=datetime.now(timezone.utc),
    )
    res_2 = learner.on_trade_closed(ticket=ticket_2, profit_usd=-1.90, active_model=active_model)
    assert res_2["learned"] is True
    assert res_2["outcome"] == "LOSS"
    assert res_2["total_experiences"] == 2


def test_experience_learner_fine_tuning(tmp_path: Path):
    mem_file = tmp_path / "test_memory_ft.json"
    model_file = str(tmp_path / "calibrated_model_ft.joblib")
    learner = ExperienceLearner(memory_file=mem_file, model_save_path=model_file)
    active_model = PredictionModel()

    vec = make_dummy_vector()

    # Simulate 6 trades to cross the fine-tuning threshold (>= 5)
    for i in range(6):
        ticket = 2000 + i
        learner.register_trade_entry(
            ticket=ticket,
            vector=vec,
            signal_type=SignalType.BUY if i % 2 == 0 else SignalType.SELL,
            entry_price=2650.0 + i,
            time_utc=datetime.now(timezone.utc),
        )
        # Alternate wins and losses
        p = 2.10 if i % 2 == 0 else -2.00
        res = learner.on_trade_closed(ticket=ticket, profit_usd=p, active_model=active_model)

    # After 6 experiences, fine-tuning should have executed
    assert len(learner.experience_bank) == 6
    assert Path(model_file).exists()
    assert active_model.is_trained is True
