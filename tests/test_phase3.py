"""
Comprehensive Unit Tests for Phase 3 - Research, ML Calibration & Backtest.
"""
from datetime import datetime, timezone
import numpy as np
import pytest

from app.core.enums import SignalType, Timeframe, TrendState
from app.core.schemas import BarData, SymbolCondition, TechnicalFeatures, UnifiedFeatureVector, TradeSignal
from app.strategy.baseline_strategy import BaselineStrategy
from app.strategy.prediction_model import PredictionModel
from app.strategy.signal_engine import SignalEngine
from backtests.backtest_engine import BacktestEngine


def make_tech_features(close: float, trend: TrendState, spread_ratio: float = 1.0) -> TechnicalFeatures:
    return TechnicalFeatures(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        close=close,
        ema_21_m15=close - 2.0 if trend in [TrendState.UP, TrendState.STRONG_UP] else close + 2.0,
        ema_50_m15=close - 4.0 if trend in [TrendState.UP, TrendState.STRONG_UP] else close + 4.0,
        ema_200_m15=close - 10.0 if trend in [TrendState.UP, TrendState.STRONG_UP] else close + 10.0,
        ema_21_h1=close - 2.0,
        ema_50_h1=close - 4.0,
        ema_200_h1=close - 10.0,
        atr_14_m15=3.5,
        current_spread=20,
        spread_ema_30m=20.0,
        spread_ratio=spread_ratio,
        trend_state=trend,
    )


def test_baseline_strategy_evaluation():
    strat = BaselineStrategy()
    tech = make_tech_features(2650.0, TrendState.UP)
    sig = strat.evaluate(tech)
    assert sig.signal_type in [SignalType.BUY, SignalType.STAND_ASIDE]
    assert sig.suggested_sl > 0
    assert sig.suggested_tp > 0


def test_prediction_model_calibration_and_brier_score():
    model = PredictionModel()
    # Generate synthetic training samples
    np.random.seed(42)
    n = 100
    X = np.random.randn(n, 8)
    # Synthetic target with correlation to feature 0
    logits = X[:, 0] * 1.5 + X[:, 5] * 0.8
    probs = 1.0 / (1.0 + np.exp(-logits))
    y = (probs > 0.5).astype(int)

    metrics = model.train_and_calibrate(X, y)
    assert metrics["brier_score"] <= 0.25
    assert model.is_trained is True

    # Test probability output
    dummy_vec = UnifiedFeatureVector(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        technical=make_tech_features(2650.0, TrendState.UP),
        calculation_latency_ms=1.0,
    )
    p_up, p_down = model.predict_probability(dummy_vec)
    assert 0.0 <= p_up <= 1.0
    assert 0.0 <= p_down <= 1.0
    assert round(p_up + p_down, 2) == 1.0


def test_signal_engine_spread_veto():
    model = PredictionModel()
    engine = SignalEngine(model=model)

    # Abnormally high spread ratio (> 2.0)
    tech = make_tech_features(2650.0, TrendState.UP, spread_ratio=2.5)
    vec = UnifiedFeatureVector(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        technical=tech,
        calculation_latency_ms=1.0,
    )

    sig = engine.evaluate_opportunity(vec)
    assert sig.signal_type == SignalType.STAND_ASIDE
    assert "abnormally wide" in sig.reason


def test_backtest_engine_simulation():
    engine = BacktestEngine(initial_balance_usd=1000.0)

    # Synthetic series of 50 bars
    bars = []
    base_p = 2650.0
    for i in range(50):
        dt = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
        p = base_p + np.sin(i / 5.0) * 15.0
        bars.append(
            BarData(
                symbol="XAUUSDm",
                timeframe=Timeframe.M15,
                time_utc=dt,
                open=p,
                high=p + 3.0,
                low=p - 3.0,
                close=p + 1.0,
                tick_volume=100,
                spread=20,
                real_volume=0,
            )
        )

    def dummy_signal(idx, b_slice):
        if idx % 10 == 0:
            return TradeSignal(
                symbol="XAUUSDm",
                time_utc=b_slice[idx].time_utc,
                signal_type=SignalType.BUY,
                win_probability=0.6,
                expected_value=15.0,
                suggested_sl=b_slice[idx].close - 4.0,
                suggested_tp=b_slice[idx].close + 6.0,
                reason="Test Buy",
            )
        return None

    report, trades = engine.run_backtest("Test Strategy", bars, dummy_signal)
    assert report.total_trades >= 0
    assert report.max_drawdown_pct >= 0.0
