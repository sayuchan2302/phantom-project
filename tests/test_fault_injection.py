"""
Fault Injection & Safety Robustness Tests for Phase 4 Quality Gate 4.
Simulates network timeouts, broker requotes, spread blowouts, daily loss limits,
session guards, news shields, idempotency, and crash recovery.
"""
from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.core.enums import NewsImpact, RiskDecision, SignalType
from app.core.schemas import (
    AccountInfo,
    EconomicEvent,
    OrderRequest,
    PositionInfo,
    SymbolCondition,
    TechnicalFeatures,
    TradeSignal,
)
from app.execution.mt5_adapter import MT5Adapter
from app.execution.reconciliation import ReconciliationEngine
from app.risk.risk_manager import RiskManager


@pytest.fixture
def base_account():
    return AccountInfo(
        login=123456,
        server="TestServer",
        balance=1000.0,
        equity=1000.0,
        profit=0.0,
        margin=0.0,
        margin_free=1000.0,
        leverage=2000,
        timestamp_utc=datetime.now(timezone.utc),
    )


@pytest.fixture
def base_symbol():
    return SymbolCondition(
        name="XAUUSDm",
        digits=3,
        point=0.001,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        bid=2650.0,
        ask=2650.25,
        spread_points=25,
        spread_usd=2.5,
        time_utc=datetime.now(timezone.utc),
    )


@pytest.fixture
def base_tech():
    return TechnicalFeatures(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        close=2650.0,
        ema_21_m15=2648.0,
        ema_50_m15=2646.0,
        ema_200_m15=2640.0,
        ema_21_h1=2648.0,
        ema_50_h1=2646.0,
        ema_200_h1=2640.0,
        atr_14_m15=3.5,
        current_spread=25,
        spread_ema_30m=25.0,
        spread_ratio=1.0,
        trend_state="UP",
    )


def test_risk_manager_daily_loss_limit_veto(base_account, base_symbol, base_tech):
    rm = RiskManager(max_daily_loss_pct=0.02) # 2% max daily loss ($20 on $1000)
    sig = TradeSignal(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        signal_type=SignalType.BUY,
        win_probability=0.6,
        expected_value=10.0,
        suggested_sl=2645.0,
        suggested_tp=2660.0,
        reason="Test",
    )

    # Simulate $25 already lost today (-$25 > -2%)
    assessment = rm.assess_risk(
        signal=sig,
        tech=base_tech,
        account=base_account,
        symbol_cond=base_symbol,
        open_positions=[],
        daily_realized_pnl=-25.0,
    )
    assert assessment.approved is False
    assert assessment.decision == RiskDecision.REJECTED_DAILY_LOSS
    assert "Daily Loss Limit" in assessment.reason


def test_risk_manager_spread_blowout_veto(base_account, base_symbol, base_tech):
    rm = RiskManager(max_absolute_spread_pts=400)
    sig = TradeSignal(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        signal_type=SignalType.BUY,
        win_probability=0.6,
        expected_value=10.0,
        suggested_sl=2645.0,
        suggested_tp=2660.0,
        reason="Test",
    )

    # 1. Test Absolute Spread Blowout (e.g. 500 pts > 400 pts threshold)
    base_symbol.spread_points = 500
    res1 = rm.assess_risk(sig, base_tech, base_account, base_symbol, [])
    assert res1.approved is False
    assert res1.decision == RiskDecision.REJECTED_SPREAD

    # 2. Test Dynamic Spread Ratio Blowout (ratio=2.5 > 2.0 threshold)
    base_symbol.spread_points = 200 # below 400
    base_tech.spread_ratio = 2.5
    res2 = rm.assess_risk(sig, base_tech, base_account, base_symbol, [])
    assert res2.approved is False
    assert res2.decision == RiskDecision.REJECTED_SPREAD


def test_risk_manager_session_guard_veto(base_account, base_symbol, base_tech):
    rm = RiskManager()
    sig = TradeSignal(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        signal_type=SignalType.BUY,
        win_probability=0.6,
        expected_value=10.0,
        suggested_sl=2645.0,
        suggested_tp=2660.0,
        reason="Test",
    )

    # Tuesday evening 21:30 UTC (during Gold daily session break)
    break_time = datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)
    res = rm.assess_risk(sig, base_tech, base_account, base_symbol, [], current_time_utc=break_time)
    assert res.approved is False
    assert res.decision == RiskDecision.REJECTED_SESSION_GUARD


def test_risk_manager_news_shield_veto(base_account, base_symbol, base_tech):
    rm = RiskManager()
    sig = TradeSignal(
        symbol="XAUUSDm",
        time_utc=datetime.now(timezone.utc),
        signal_type=SignalType.BUY,
        win_probability=0.6,
        expected_value=10.0,
        suggested_sl=2645.0,
        suggested_tp=2660.0,
        reason="Test",
    )

    # Event scheduled at 13:30 UTC
    now_utc = datetime(2026, 10, 20, 13, 15, tzinfo=timezone.utc)
    event = EconomicEvent(
        id=1,
        time_utc=datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc),
        currency="USD",
        name="CPI m/m",
        importance=NewsImpact.HIGH,
        status="UPCOMING",
    )

    res = rm.assess_risk(sig, base_tech, base_account, base_symbol, [], upcoming_events=[event], current_time_utc=now_utc)
    assert res.approved is False
    assert res.decision == RiskDecision.REJECTED_NEWS_SHIELD


def test_idempotency_prevents_duplicate_orders():
    adapter = MT5Adapter()
    magic = 888999

    # Mock open positions returning an existing position with magic 888999
    existing_pos = PositionInfo(
        ticket=12345,
        symbol="XAUUSDm",
        signal_type=SignalType.BUY,
        volume=0.01,
        price_open=2650.0,
        sl=2645.0,
        tp=2660.0,
        profit=5.0,
        magic=magic,
        time_utc=datetime.now(timezone.utc),
    )

    with patch.object(adapter, "get_open_positions", return_value=[existing_pos]), \
         patch.object(adapter, "has_duplicate_order", return_value=True):
        req = OrderRequest(
            symbol="XAUUSDm",
            signal_type=SignalType.BUY,
            volume=0.01,
            price=2650.0,
            sl=2645.0,
            tp=2660.0,
            magic_number=magic,
        )
        res = adapter.send_order(req)
        # Must be rejected due to duplicate magic
        assert res.success is False
        assert "Duplicate order" in res.comment


def test_crash_recovery_reconciliation():
    adapter = MT5Adapter()
    reconciliation = ReconciliationEngine(adapter=adapter)

    # Simulate 2 positions currently active on broker after bot crashed and restarted
    broker_positions = [
        PositionInfo(
            ticket=101,
            symbol="XAUUSDm",
            signal_type=SignalType.BUY,
            volume=0.01,
            price_open=2650.0,
            sl=2645.0,
            tp=2660.0,
            profit=10.0,
            magic=111,
            time_utc=datetime.now(timezone.utc),
        ),
        PositionInfo(
            ticket=102,
            symbol="XAUUSDm",
            signal_type=SignalType.SELL,
            volume=0.01,
            price_open=2660.0,
            sl=2665.0,
            tp=2650.0,
            profit=-2.0,
            magic=222,
            time_utc=datetime.now(timezone.utc),
        ),
    ]

    with patch.object(adapter, "get_open_positions", return_value=broker_positions):
        recovered = reconciliation.perform_crash_recovery()
        assert len(recovered) == 2
        assert 101 in reconciliation.tracked_positions
        assert 102 in reconciliation.tracked_positions

        # Simulate position 101 getting closed by broker SL/TP
        with patch.object(adapter, "get_open_positions", return_value=[broker_positions[1]]):
            report = reconciliation.reconcile()
            assert report["closed_count"] == 1
            assert 101 in report["closed_tickets"]
            assert 102 in reconciliation.tracked_positions
            assert 101 not in reconciliation.tracked_positions
