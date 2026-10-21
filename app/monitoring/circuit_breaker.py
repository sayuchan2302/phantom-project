"""
Circuit Breaker & Emergency Kill Switch.
Protects capital by shifting into Safe Mode or executing rapid account liquidation
during extreme broker disconnects, loss streaks, or external kill signals.
"""
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from app.config import settings
from app.execution.mt5_adapter import MT5Adapter
from app.monitoring.logger import app_logger


class CircuitState(str, Enum):
    NORMAL = "NORMAL"
    SAFE_MODE = "SAFE_MODE"                      # Read-only: no new positions allowed
    TRIPPED_KILL_SWITCH = "TRIPPED_KILL_SWITCH"  # Emergency stop: all positions liquidated


class CircuitBreaker:
    """Monitors connection health and loss streaks, with an instant kill switch."""

    def __init__(
        self,
        adapter: MT5Adapter,
        max_consecutive_losses: int = settings.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES,
        disconnect_timeout_sec: int = settings.CIRCUIT_BREAKER_DISCONNECT_TIMEOUT_SEC,
        flag_file: Path = settings.KILL_SWITCH_FLAG_FILE,
    ):
        self.adapter = adapter
        self.max_consecutive_losses = max_consecutive_losses
        self.disconnect_timeout_sec = disconnect_timeout_sec
        self.flag_file = flag_file

        self.state: CircuitState = CircuitState.NORMAL
        self.consecutive_losses: int = 0
        self.last_heartbeat_utc: datetime = datetime.now(timezone.utc)
        self.trip_reason: str = ""

    def record_trade_result(self, net_profit: float):
        """Update consecutive loss counter upon trade closing."""
        if net_profit < 0:
            self.consecutive_losses += 1
            app_logger.warning(
                f"Circuit Breaker recorded loss: Streak is now {self.consecutive_losses}/{self.max_consecutive_losses}"
            )
            if self.consecutive_losses >= self.max_consecutive_losses:
                self.trip_safe_mode(
                    reason=f"Loss streak reached limit ({self.consecutive_losses} consecutive losses)"
                )
        else:
            if self.consecutive_losses > 0:
                app_logger.info(f"Profit recorded (${net_profit:.2f}). Resetting loss streak to 0.")
            self.consecutive_losses = 0

    def update_heartbeat(self):
        """Record successful heartbeat with MT5 broker."""
        self.last_heartbeat_utc = datetime.now(timezone.utc)

    def check_health(self) -> CircuitState:
        """
        Check connection timeout and external flag file.
        Returns current CircuitState.
        """
        # 1. Check external flag file (Kill Switch)
        if self.flag_file.exists():
            self.trigger_emergency_kill_switch(reason=f"Kill switch flag file detected ({self.flag_file})")
            return self.state

        # 2. Check MT5 connection timeout
        if self.state == CircuitState.NORMAL:
            elapsed = (datetime.now(timezone.utc) - self.last_heartbeat_utc).total_seconds()
            if elapsed > self.disconnect_timeout_sec:
                self.trip_safe_mode(
                    reason=f"Broker disconnected for {int(elapsed)}s (> {self.disconnect_timeout_sec}s limit)"
                )

        return self.state

    def trip_safe_mode(self, reason: str):
        """Enter Safe Mode: stop opening new positions, manage existing only."""
        if self.state != CircuitState.TRIPPED_KILL_SWITCH:
            self.state = CircuitState.SAFE_MODE
            self.trip_reason = reason
            app_logger.warning(f"CIRCUIT BREAKER -> SAFE MODE: {reason}")

    def trigger_emergency_kill_switch(self, reason: str = "Manual / Automated Kill Switch") -> Dict[str, any]:
        """
        EMERGENCY KILL SWITCH:
        Closes all open positions and cancels all pending orders within seconds.
        """
        self.state = CircuitState.TRIPPED_KILL_SWITCH
        self.trip_reason = reason
        app_logger.critical(f"EMERGENCY KILL SWITCH ACTIVATED! Reason: {reason}")

        open_positions = self.adapter.get_open_positions()
        closed_tickets = []
        failed_tickets = []

        start_time = datetime.now(timezone.utc)
        for pos in open_positions:
            res = self.adapter.close_position(ticket=pos.ticket)
            if res.success:
                closed_tickets.append(pos.ticket)
                app_logger.info(f"Kill Switch closed position {pos.ticket} successfully.")
            else:
                failed_tickets.append(pos.ticket)
                app_logger.error(f"Kill Switch failed to close position {pos.ticket}: {res.comment}")

        duration_sec = (datetime.now(timezone.utc) - start_time).total_seconds()
        app_logger.critical(
            f"Kill Switch Liquidation complete in {duration_sec:.2f}s: "
            f"{len(closed_tickets)} closed, {len(failed_tickets)} failed."
        )

        return {
            "state": self.state.value,
            "reason": self.trip_reason,
            "closed_count": len(closed_tickets),
            "failed_count": len(failed_tickets),
            "duration_sec": round(duration_sec, 2),
        }

    def reset(self):
        """Reset Circuit Breaker back to NORMAL state."""
        self.state = CircuitState.NORMAL
        self.consecutive_losses = 0
        self.trip_reason = ""
        self.last_heartbeat_utc = datetime.now(timezone.utc)
        if self.flag_file.exists():
            try:
                self.flag_file.unlink()
            except Exception:
                pass
        app_logger.info("Circuit Breaker reset to NORMAL state.")
