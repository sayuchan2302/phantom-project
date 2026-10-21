"""
Comprehensive Unit Tests for Phase 5 - Monitoring, Circuit Breaker & Performance.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.core.enums import SignalType
from app.core.schemas import OrderResult, PositionInfo
from app.execution.mt5_adapter import MT5Adapter
from app.monitoring.circuit_breaker import CircuitBreaker, CircuitState
from app.monitoring.performance import PerformanceTracker
from app.monitoring.telegram_bot import TelegramNotifier


def test_circuit_breaker_loss_streak_safe_mode():
    adapter = MagicMock(spec=MT5Adapter)
    cb = CircuitBreaker(adapter=adapter, max_consecutive_losses=3)

    assert cb.state == CircuitState.NORMAL
    cb.record_trade_result(-10.0) # Loss 1
    assert cb.consecutive_losses == 1
    assert cb.state == CircuitState.NORMAL

    cb.record_trade_result(-15.0) # Loss 2
    assert cb.consecutive_losses == 2
    assert cb.state == CircuitState.NORMAL

    cb.record_trade_result(-20.0) # Loss 3 -> Should trip safe mode!
    assert cb.consecutive_losses == 3
    assert cb.state == CircuitState.SAFE_MODE
    assert "Loss streak" in cb.trip_reason

    # Reset
    cb.reset()
    assert cb.state == CircuitState.NORMAL
    assert cb.consecutive_losses == 0


def test_circuit_breaker_heartbeat_timeout():
    adapter = MagicMock(spec=MT5Adapter)
    cb = CircuitBreaker(adapter=adapter, disconnect_timeout_sec=30)

    # Set last heartbeat to 45 seconds ago
    cb.last_heartbeat_utc = datetime.now(timezone.utc) - timedelta(seconds=45)
    state = cb.check_health()

    assert state == CircuitState.SAFE_MODE
    assert "disconnected for" in cb.trip_reason


def test_circuit_breaker_emergency_kill_switch(tmp_path: Path):
    adapter = MagicMock(spec=MT5Adapter)
    fake_pos = [
        PositionInfo(
            ticket=555,
            symbol="XAUUSDm",
            signal_type=SignalType.BUY,
            volume=0.01,
            price_open=2650.0,
            sl=2640.0,
            tp=2670.0,
            profit=-5.0,
            magic=123,
            time_utc=datetime.now(timezone.utc),
        )
    ]
    adapter.get_open_positions.return_value = fake_pos
    adapter.close_position.return_value = OrderResult(success=True, ticket=555)

    flag_file = tmp_path / "kill_switch.flag"
    cb = CircuitBreaker(adapter=adapter, flag_file=flag_file)

    # Trigger via flag file
    flag_file.touch()
    state = cb.check_health()

    assert state == CircuitState.TRIPPED_KILL_SWITCH
    adapter.close_position.assert_called_once_with(ticket=555)


def test_telegram_notifier_fallback():
    # When no token provided, notifier should log locally and return True without throwing
    notifier = TelegramNotifier(bot_token=None, chat_id=None)
    assert notifier.is_configured is False

    res = notifier.send_message("Test Alert Message")
    assert res is True

    # Check notification formatters don't raise
    order = OrderResult(
        success=True,
        ticket=1001,
        volume=0.01,
        price=2650.5,
        magic_number=777,
        comment="BUY_TEST",
    )
    notifier.notify_order_opened(order, sl=2645.0, tp=2660.0)
    notifier.notify_order_closed(ticket=1001, profit=15.0, reason="TP")
    notifier.notify_risk_veto(decision="REJECTED_SPREAD", reason="Spread > 400")
    notifier.notify_circuit_breaker(state="SAFE_MODE", reason="Timeout")
    notifier.notify_daily_summary(balance=1000.0, daily_pnl=15.0, trades_count=3, win_rate=66.7, max_dd=1.2)


def test_performance_tracker_slippage_and_drift():
    tracker = PerformanceTracker(backtest_assumed_slippage_pips=0.8, backtest_winrate=50.0)

    # 1. Test Slippage recording
    # Requested 2650.000, Filled 2650.015 -> Diff = 0.015 -> 0.015 / (0.001 * 10) = 1.5 pips
    slip = tracker.record_execution(requested_price=2650.000, filled_price=2650.015, point_value=0.001)
    assert slip == 1.5

    stats = tracker.get_slippage_stats()
    assert stats["count"] == 1
    assert stats["avg_slippage_pips"] == 1.5

    # 2. Test Drift Detection
    # Record 5 losses (winrate = 0%, backtest = 50% -> diff = 50% > 20% threshold)
    for _ in range(5):
        tracker.record_closed_trade(-10.0)

    drift = tracker.detect_performance_drift()
    assert drift["drift_detected"] is True
    assert drift["live_winrate"] == 0.0
    assert "below backtest" in drift["reason"]
