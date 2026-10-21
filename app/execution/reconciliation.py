"""
Reconciliation Engine - Broker as Single Source of Truth (SSOT).
Periodically synchronizes internal state with live broker positions,
handles crash recovery upon restart, and tracks positions closed by SL/TP.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional
import MetaTrader5 as mt5

from app.core.schemas import PositionInfo
from app.execution.mt5_adapter import MT5Adapter
from app.monitoring.logger import app_logger


class ReconciliationEngine:
    """Synchronizes bot position state with MT5 Server (SSOT) and handles crash recovery."""

    def __init__(self, adapter: MT5Adapter):
        self.adapter = adapter
        self.tracked_positions: Dict[int, PositionInfo] = {}
        self.recovered_on_startup: List[int] = []

    def perform_crash_recovery(self) -> List[PositionInfo]:
        """
        Executed on startup: Scans MT5 broker for any open positions from previous runs.
        Ensures the bot does not open duplicate orders after a crash or restart.
        """
        app_logger.info("Executing Crash Recovery scan on MT5 broker...")
        broker_positions = self.adapter.get_open_positions()

        self.tracked_positions.clear()
        self.recovered_on_startup.clear()

        for pos in broker_positions:
            self.tracked_positions[pos.ticket] = pos
            self.recovered_on_startup.append(pos.ticket)
            app_logger.warning(
                f"CRASH RECOVERY: Recovered active position from broker: Ticket={pos.ticket}, "
                f"Symbol={pos.symbol}, Type={pos.signal_type.value}, Lot={pos.volume}, "
                f"OpenPrice={pos.price_open}, Magic={pos.magic}, Profit=${pos.profit:.2f}"
            )

        app_logger.info(f"Crash Recovery completed. {len(self.recovered_on_startup)} positions adopted.")
        return list(self.tracked_positions.values())

    def reconcile(self) -> Dict[str, any]:
        """
        Periodic reconciliation:
        1. Queries MT5 server for live positions.
        2. Detects any positions closed by broker (SL hit, TP hit, Stop Out).
        3. Updates internal memory.
        """
        broker_positions = self.adapter.get_open_positions()
        broker_tickets = {p.ticket: p for p in broker_positions}

        new_positions = []
        closed_tickets = []

        # Check for newly discovered positions
        for ticket, pos in broker_tickets.items():
            if ticket not in self.tracked_positions:
                new_positions.append(ticket)
                self.tracked_positions[ticket] = pos
            else:
                # Update floating profit and SL/TP
                self.tracked_positions[ticket] = pos

        # Check for closed positions (were tracked, now gone from broker)
        for ticket in list(self.tracked_positions.keys()):
            if ticket not in broker_tickets:
                closed_tickets.append(ticket)
                closed_pos = self.tracked_positions.pop(ticket)
                app_logger.info(
                    f"Position {ticket} has been closed on broker (SL/TP or Manual). Removed from tracking."
                )

        return {
            "active_count": len(self.tracked_positions),
            "new_count": len(new_positions),
            "closed_count": len(closed_tickets),
            "active_tickets": list(self.tracked_positions.keys()),
            "closed_tickets": closed_tickets,
        }
