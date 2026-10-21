"""
Master Autonomous Trading Loop.
Coordinates all modules across Data, Intelligence, Strategy, Risk, Execution, and Monitoring.
"""
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from app.config import settings
from app.core.enums import SignalType, Timeframe
from app.core.schemas import OrderRequest, OrderResult
from app.data.data_validator import DataValidator
from app.data.economic_calendar import EconomicCalendarCollector
from app.data.storage import SQLiteStorage
from app.execution.mt5_adapter import MT5Adapter
from app.execution.reconciliation import ReconciliationEngine
from app.intelligence.feature_store import FeatureStore
from app.intelligence.market_analyzer import MarketAnalyzer
from app.intelligence.news_extractor import NewsExtractor
from app.monitoring.circuit_breaker import CircuitBreaker, CircuitState
from app.monitoring.logger import app_logger
from app.monitoring.performance import PerformanceTracker
from app.monitoring.telegram_bot import TelegramNotifier
from app.risk.risk_manager import RiskManager
from app.strategy.adaptive_manager import AdaptiveRetrainer
from app.strategy.prediction_model import PredictionModel
from app.strategy.signal_engine import SignalEngine


class AutonomousTradingBot:
    """Master Orchestrator controlling the end-to-end trading loop."""

    def __init__(self):
        # 1. Execution & Broker Connectivity
        self.adapter = MT5Adapter()
        self.reconciliation = ReconciliationEngine(adapter=self.adapter)

        # 2. Storage & Validation
        self.storage = SQLiteStorage()
        self.validator = DataValidator()

        # 3. Intelligence & Feature Engineering
        self.analyzer = MarketAnalyzer()
        self.calendar = EconomicCalendarCollector()
        self.news_extractor = NewsExtractor()
        self.feature_store = FeatureStore()

        # 4. Strategy & Expected Value
        self.model = PredictionModel()
        self.signal_engine = SignalEngine(model=self.model)

        # 5. Risk & Capital Defense
        self.risk_manager = RiskManager()

        # 6. Monitoring & Failsafes
        self.circuit_breaker = CircuitBreaker(adapter=self.adapter)
        self.notifier = TelegramNotifier()
        self.performance = PerformanceTracker()
        self.adaptive_retrainer = AdaptiveRetrainer(lookback_bars=3000, cooldown_minutes=30)

        self.is_running = False

    def initialize(self) -> bool:
        """Connect to MT5, recover active positions, and perform startup calibration."""
        app_logger.info("Initializing Autonomous Trading Bot...")
        if not self.adapter.connect():
            app_logger.error("Failed to connect to MT5 during bot initialization.")
            return False

        # Load trained prediction model if exists
        self.model.load_model("models/calibrated_model.joblib")

        # Broker SSOT Crash Recovery
        self.reconciliation.perform_crash_recovery()
        self.circuit_breaker.update_heartbeat()
        app_logger.info("Bot successfully initialized and ready.")
        return True

    def run_iteration(self) -> Dict[str, any]:
        """
        Execute a single end-to-end cycle with strict defense:
        1. Heartbeat & Circuit Breaker Health Check
        2. Broker Reconciliation (SSOT)
        3. Market Feed & Feature Extraction
        4. Strategy Opportunity Evaluation (EV > 0)
        5. Risk Manager Veto Assessment
        6. Order Execution & Hard SL Placement
        """
        cycle_start = time.perf_counter()
        now_utc = datetime.now(timezone.utc)

        # Step 1: Health & Circuit Breaker Check
        circuit_status = self.circuit_breaker.check_health()
        if circuit_status == CircuitState.TRIPPED_KILL_SWITCH:
            app_logger.critical("Bot paused: Kill Switch is tripped. Call reset() to resume.")
            return {"status": "TRIPPED_KILL_SWITCH"}

        # Step 2: Broker SSOT Reconciliation
        recon_report = self.reconciliation.reconcile()
        self.circuit_breaker.update_heartbeat()

        # Step 2b: Adaptive Auto Re-training Check (Self-Healing on loss streaks or drift)
        drift_status = self.performance.detect_performance_drift()
        should_retrain, trigger_reason = self.adaptive_retrainer.should_trigger_retraining(
            circuit_state=circuit_status,
            consecutive_losses=self.circuit_breaker.consecutive_losses,
            drift_detected=drift_status.get("drift_detected", False),
            current_time_utc=now_utc,
        )

        if should_retrain:
            app_logger.info(f"Adaptive Re-training triggered: {trigger_reason}")
            retrain_res = self.adaptive_retrainer.execute_adaptive_retraining(
                adapter=self.adapter,
                active_model=self.model,
                current_time_utc=now_utc,
            )
            if retrain_res.get("promoted"):
                # Reset circuit breaker safe mode since new model passed validation
                self.circuit_breaker.reset()
                circuit_status = CircuitState.NORMAL
                self.notifier.send_message(
                    f"🚀 *ADAPTIVE MODEL PROMOTED*\n"
                    f"• Brier Score: `{retrain_res.get('candidate_test_brier')}`\n"
                    f"• Samples: `{retrain_res.get('train_samples')}` Train / `{retrain_res.get('test_samples')}` Test\n"
                    f"• Trading Resumed in NORMAL mode!"
                )
            else:
                self.notifier.send_message(
                    f"⚠️ *ADAPTIVE RETRAINING REJECTED*\n"
                    f"• Reason: `{retrain_res.get('reason')}`\n"
                    f"• Safe Mode retained to preserve capital."
                )

        if circuit_status == CircuitState.SAFE_MODE:
            app_logger.warning("Bot in Safe Mode: Monitoring existing positions only.")

        # Step 3: Market Data & Symbol Inspection
        sym = self.adapter.detect_gold_symbol()
        account = self.adapter.get_account_info()
        cond = self.adapter.get_symbol_condition(sym)
        bars_m15 = self.adapter.get_bars(symbol=sym, timeframe=Timeframe.M15, count=150)
        bars_h1 = self.adapter.get_bars(symbol=sym, timeframe=Timeframe.H1, count=50)

        if len(bars_m15) < 30:
            app_logger.warning("Insufficient bars for iteration.")
            return {"status": "WAITING_FOR_DATA"}

        # Step 4: Technical & Macro Feature Extraction
        tech = self.analyzer.analyze(symbol=sym, bars_m15=bars_m15, bars_h1=bars_h1, current_condition=cond)
        upcoming_events = self.calendar.fetch_events(currency="USD")

        # Save to feature store
        vec = self.feature_store.build_and_save_features(
            technical=tech,
            start_clock=cycle_start,
        )

        # Step 5: Strategy Evaluation (EV Check)
        if circuit_status == CircuitState.SAFE_MODE:
            return {"status": "SAFE_MODE_MONITORING", "active_positions": recon_report["active_count"]}

        signal = self.signal_engine.evaluate_opportunity(vec, lot_size=cond.volume_min)

        if signal.signal_type == SignalType.STAND_ASIDE:
            return {
                "status": "STAND_ASIDE",
                "reason": signal.reason,
                "close": tech.close,
                "spread": tech.current_spread,
                "win_prob": signal.win_probability,
                "ev": signal.expected_value,
            }

        # Step 6: Risk Manager Veto Assessment
        open_pos = list(self.reconciliation.tracked_positions.values())
        risk_eval = self.risk_manager.assess_risk(
            signal=signal,
            tech=tech,
            account=account,
            symbol_cond=cond,
            open_positions=open_pos,
            upcoming_events=upcoming_events,
            current_time_utc=now_utc,
        )

        if not risk_eval.approved:
            self.notifier.notify_risk_veto(decision=risk_eval.decision.value, reason=risk_eval.reason)
            return {"status": "VETOED", "decision": risk_eval.decision.value, "reason": risk_eval.reason}

        # Step 7: Execution via MT5 with Unique Magic Number & Hard Stops
        magic = int(time.time()) % 1_000_000 # Unique Magic per opportunity
        order_req = OrderRequest(
            symbol=sym,
            signal_type=signal.signal_type,
            volume=risk_eval.calculated_lot,
            price=tech.close,
            sl=signal.suggested_sl,
            tp=signal.suggested_tp,
            magic_number=magic,
            comment="AI_MODULAR_BOT",
        )

        order_res = self.adapter.send_order(order_req)
        if order_res.success:
            self.notifier.notify_order_opened(order=order_res, sl=signal.suggested_sl, tp=signal.suggested_tp)
            # Reconcile immediately to track the newly opened position
            self.reconciliation.reconcile()
            return {"status": "EXECUTED", "ticket": order_res.ticket, "volume": order_res.volume}
        else:
            return {"status": "EXECUTION_REJECTED", "retcode": order_res.retcode, "comment": order_res.comment}

    def run_loop(self, interval_sec: int = 30):
        """Continuously run trading iterations every interval_sec until interrupted."""
        if not self.initialize():
            app_logger.error("Failed to initialize bot. Exiting loop.")
            return

        self.is_running = True
        app_logger.info(f"Bot loop started! Running iteration every {interval_sec}s. Press Ctrl+C to stop.")

        try:
            iteration_count = 0
            while self.is_running:
                iteration_count += 1
                res = self.run_iteration()
                st = res.get("status")
                if st == "STAND_ASIDE":
                    app_logger.info(
                        f"[Cycle #{iteration_count}] STAND_ASIDE | Giá: {res.get('close')} | Spread: {res.get('spread')} pts | "
                        f"Prob: {res.get('win_prob', 0):.1%} | EV: ${res.get('ev', 0):.2f} | {res.get('reason')}"
                    )
                elif st == "EXECUTED":
                    app_logger.info(
                        f"[Cycle #{iteration_count}] EXECUTED | Ticket: {res.get('ticket')} | Volume: {res.get('volume')} lot"
                    )
                elif st == "VETOED":
                    app_logger.warning(f"[Cycle #{iteration_count}] VETOED by Risk Manager: {res.get('reason')}")
                elif st == "SAFE_MODE_MONITORING":
                    app_logger.warning(f"[Cycle #{iteration_count}] CIRCUIT BREAKER SAFE MODE ACTIVE. Monitoring...")

                time.sleep(interval_sec)
        except KeyboardInterrupt:
            app_logger.info("Bot loop interrupted by user (Ctrl+C).")
        finally:
            self.shutdown()

    def shutdown(self):
        """Clean shutdown of all services."""
        app_logger.info("Shutting down Autonomous Trading Bot...")
        self.is_running = False
        self.adapter.disconnect()


if __name__ == "__main__":
    bot = AutonomousTradingBot()
    bot.run_loop(interval_sec=30)
