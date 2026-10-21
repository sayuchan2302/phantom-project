"""
MT5 Adapter - Read & Execution Engine for Phase 4.
Handles broker connection, market feeds, idempotent order dispatch with hard stops,
and live position tracking (Broker as SSOT).
"""
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple
import MetaTrader5 as mt5

from app.config import settings
from app.core.constants import DEFAULT_GOLD_SYMBOLS
from app.core.enums import ConnectionState, SignalType, Timeframe
from app.core.schemas import (
    AccountInfo,
    BarData,
    OrderRequest,
    OrderResult,
    PositionInfo,
    SymbolCondition,
    TickData,
)
from app.monitoring.logger import app_logger


TIMEFRAME_MAP = {
    Timeframe.M1: mt5.TIMEFRAME_M1,
    Timeframe.M5: mt5.TIMEFRAME_M5,
    Timeframe.M15: mt5.TIMEFRAME_M15,
    Timeframe.M30: mt5.TIMEFRAME_M30,
    Timeframe.H1: mt5.TIMEFRAME_H1,
    Timeframe.H4: mt5.TIMEFRAME_H4,
    Timeframe.D1: mt5.TIMEFRAME_D1,
}


class MT5Adapter:
    """Full-featured MT5 Adapter providing safe execution and market data reading."""

    def __init__(self):
        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self.active_symbol: Optional[str] = None

    def connect(self) -> bool:
        """Initialize connection and login to Exness MT5 account."""
        app_logger.info("Initializing MT5 Terminal connection...")
        self.state = ConnectionState.CONNECTING

        init_kwargs = {}
        if settings.MT5_PATH:
            init_kwargs["path"] = settings.MT5_PATH

        if not mt5.initialize(**init_kwargs):
            error_code, error_desc = mt5.last_error()
            app_logger.error(f"MT5 initialize failed: code={error_code}, desc={error_desc}")
            self.state = ConnectionState.ERROR
            return False

        app_logger.info(f"Logging in to MT5 Server: {settings.MT5_SERVER} with Account: {settings.MT5_LOGIN}")
        authorized = mt5.login(
            login=settings.MT5_LOGIN,
            password=settings.MT5_PASSWORD,
            server=settings.MT5_SERVER,
        )

        if not authorized:
            error_code, error_desc = mt5.last_error()
            app_logger.error(f"MT5 login failed: code={error_code}, desc={error_desc}")
            self.state = ConnectionState.ERROR
            return False

        self.state = ConnectionState.CONNECTED
        terminal_info = mt5.terminal_info()
        app_logger.info(
            f"MT5 connected successfully! Terminal build: {terminal_info.build if terminal_info else 'N/A'}, "
            f"Connected: {terminal_info.connected if terminal_info else False}"
        )
        return True

    def disconnect(self):
        """Shutdown MT5 connection cleanly."""
        mt5.shutdown()
        self.state = ConnectionState.DISCONNECTED
        app_logger.info("MT5 disconnected cleanly.")

    def get_account_info(self) -> AccountInfo:
        """Fetch current broker account details directly from MT5 Server."""
        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError(f"Failed to fetch account_info: {mt5.last_error()}")

        return AccountInfo(
            login=acc.login,
            server=acc.server,
            currency=acc.currency,
            balance=acc.balance,
            equity=acc.equity,
            profit=acc.profit,
            margin=acc.margin,
            margin_free=acc.margin_free,
            margin_level=acc.margin_level,
            leverage=acc.leverage,
            timestamp_utc=datetime.now(timezone.utc),
        )

    def detect_gold_symbol(self) -> str:
        """Auto-detect which Gold symbol name is available on this Exness account."""
        if self.active_symbol:
            return self.active_symbol

        candidates = [settings.TARGET_SYMBOL] + DEFAULT_GOLD_SYMBOLS
        for sym in candidates:
            info = mt5.symbol_info(sym)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(sym, True)
                self.active_symbol = sym
                app_logger.info(f"Detected and selected active Gold symbol: {sym}")
                return sym

        raise RuntimeError(f"None of the Gold symbols {candidates} found on server {settings.MT5_SERVER}")

    def get_symbol_condition(self, symbol: Optional[str] = None) -> SymbolCondition:
        """Read live symbol trading specifications and real-time spread."""
        sym_name = symbol or self.detect_gold_symbol()
        info = mt5.symbol_info(sym_name)
        if info is None:
            raise RuntimeError(f"Failed to get symbol_info for {sym_name}: {mt5.last_error()}")

        spread_points = info.spread
        spread_usd = spread_points * info.point * info.trade_contract_size

        return SymbolCondition(
            name=sym_name,
            digits=info.digits,
            point=info.point,
            contract_size=info.trade_contract_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            bid=info.bid,
            ask=info.ask,
            spread_points=spread_points,
            spread_usd=round(spread_usd, 2),
            time_utc=datetime.fromtimestamp(info.time, tz=timezone.utc),
        )

    def get_bars(self, symbol: Optional[str] = None, timeframe: Timeframe = Timeframe.M15, count: int = 200) -> List[BarData]:
        """Fetch historical bars directly converted to UTC."""
        sym_name = symbol or self.detect_gold_symbol()
        tf_mt5 = TIMEFRAME_MAP[timeframe]

        rates = mt5.copy_rates_from_pos(sym_name, tf_mt5, 0, count)
        if rates is None or len(rates) == 0:
            return []

        bars: List[BarData] = []
        for r in rates:
            dt_utc = datetime.fromtimestamp(r[0], tz=timezone.utc)
            bars.append(
                BarData(
                    symbol=sym_name,
                    timeframe=timeframe,
                    time_utc=dt_utc,
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    tick_volume=int(r[5]),
                    spread=int(r[6]),
                    real_volume=int(r[7]),
                )
            )
        return bars

    def get_ticks(self, symbol: Optional[str] = None, count: int = 100) -> List[TickData]:
        """Fetch latest raw tick data."""
        sym_name = symbol or self.detect_gold_symbol()
        raw_ticks = mt5.copy_ticks_from(sym_name, datetime.now(timezone.utc), count, mt5.COPY_TICKS_ALL)
        if raw_ticks is None or len(raw_ticks) == 0:
            return []

        ticks: List[TickData] = []
        for t in raw_ticks:
            dt_utc = datetime.fromtimestamp(t[0], tz=timezone.utc)
            ticks.append(
                TickData(
                    symbol=sym_name,
                    time_utc=dt_utc,
                    bid=float(t[1]),
                    ask=float(t[2]),
                    last=float(t[3]),
                    volume=float(t[4]),
                    flags=int(t[6]) if len(t) > 6 else 0,
                )
            )
        return ticks

    # =========================================================================
    # PHASE 4: EXECUTION & POSITION TRACKING METHODS
    # =========================================================================

    def get_open_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> List[PositionInfo]:
        """
        Query all currently open positions from broker (SSOT).
        Optionally filter by symbol or magic number.
        """
        sym_filter = symbol or self.detect_gold_symbol()
        raw_positions = mt5.positions_get(symbol=sym_filter)
        if raw_positions is None:
            return []

        positions: List[PositionInfo] = []
        for p in raw_positions:
            if magic is not None and p.magic != magic:
                continue

            pos_type = SignalType.BUY if p.type == mt5.ORDER_TYPE_BUY else SignalType.SELL
            positions.append(
                PositionInfo(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    signal_type=pos_type,
                    volume=p.volume,
                    price_open=p.price_open,
                    sl=p.sl,
                    tp=p.tp,
                    profit=p.profit,
                    magic=p.magic,
                    time_utc=datetime.fromtimestamp(p.time, tz=timezone.utc),
                )
            )
        return positions

    def has_duplicate_order(self, magic_number: int, symbol: Optional[str] = None) -> bool:
        """
        Idempotency check: returns True if a position or order with magic_number already exists.
        """
        positions = self.get_open_positions(symbol=symbol, magic=magic_number)
        if len(positions) > 0:
            return True

        # Also check pending orders
        pending = mt5.orders_get(symbol=symbol or self.detect_gold_symbol())
        if pending:
            for o in pending:
                if o.magic == magic_number:
                    return True
        return False

    def send_order(self, req: OrderRequest, max_retries: int = 2) -> OrderResult:
        """
        Dispatches order to MT5 broker with Hard Stop Loss and idempotency check.
        Retries on REQUOTE or TIMEOUT.
        """
        # 1. Idempotency Check
        if self.has_duplicate_order(req.magic_number, req.symbol):
            app_logger.warning(
                f"IDEMPOTENCY TRIGGERED: Active position or order with Magic={req.magic_number} already exists! "
                f"Aborting duplicate send."
            )
            return OrderResult(
                success=False,
                retcode=10027, # Duplicate order code
                comment=f"Duplicate order with Magic {req.magic_number} already exists",
                magic_number=req.magic_number,
            )

        sym_info = mt5.symbol_info(req.symbol)
        if sym_info is None:
            raise RuntimeError(f"Cannot get symbol_info for {req.symbol}")

        # Choose filling mode supported by symbol
        filling = mt5.ORDER_FILLING_IOC
        if sym_info.filling_mode & mt5.ORDER_FILLING_FOK:
            filling = mt5.ORDER_FILLING_FOK
        elif sym_info.filling_mode & mt5.ORDER_FILLING_IOC:
            filling = mt5.ORDER_FILLING_IOC
        elif sym_info.filling_mode & mt5.ORDER_FILLING_RETURN:
            filling = mt5.ORDER_FILLING_RETURN

        order_type = mt5.ORDER_TYPE_BUY if req.signal_type == SignalType.BUY else mt5.ORDER_TYPE_SELL

        for attempt in range(max_retries + 1):
            tick = mt5.symbol_info_tick(req.symbol)
            if tick is None:
                return OrderResult(success=False, comment="Failed to get tick", magic_number=req.magic_number)

            price = tick.ask if req.signal_type == SignalType.BUY else tick.bid

            request_dict = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": req.symbol,
                "volume": req.volume,
                "type": order_type,
                "price": price,
                "sl": req.sl,
                "tp": req.tp,
                "deviation": 25,
                "magic": req.magic_number,
                "comment": req.comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }

            app_logger.info(
                f"Sending Order to MT5 [Attempt {attempt+1}]: {req.signal_type.value} {req.volume} lot {req.symbol} "
                f"@ {price} (SL={req.sl}, TP={req.tp}, Magic={req.magic_number})"
            )

            result = mt5.order_send(request_dict)
            if result is None:
                app_logger.error(f"order_send returned None: {mt5.last_error()}")
                return OrderResult(success=False, comment="order_send returned None", magic_number=req.magic_number)

            # Check if execution succeeded
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                app_logger.info(
                    f"ORDER EXECUTED! Ticket={result.order}, Volume={result.volume}, "
                    f"Price={result.price}, Magic={req.magic_number}"
                )
                return OrderResult(
                    success=True,
                    ticket=result.order,
                    volume=result.volume,
                    price=result.price,
                    retcode=result.retcode,
                    comment=result.comment,
                    magic_number=req.magic_number,
                    timestamp_utc=datetime.now(timezone.utc),
                )

            # Check for recoverable errors (Requote, Timeout)
            if result.retcode in [mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_TIMEOUT] and attempt < max_retries:
                app_logger.warning(f"Order failed with retcode {result.retcode} ({result.comment}). Retrying in 1s...")
                time.sleep(1.0)
                continue

            app_logger.error(
                f"Order REJECTED by broker: retcode={result.retcode}, comment='{result.comment}'"
            )
            return OrderResult(
                success=False,
                retcode=result.retcode,
                comment=result.comment,
                magic_number=req.magic_number,
            )

        return OrderResult(success=False, comment="Max retries exceeded", magic_number=req.magic_number)

    def close_position(self, ticket: int) -> OrderResult:
        """Close an open position by ticket."""
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return OrderResult(success=False, comment=f"Position {ticket} not found")

        pos = positions[0]
        sym_info = mt5.symbol_info(pos.symbol)
        tick = mt5.symbol_info_tick(pos.symbol)
        if sym_info is None or tick is None:
            return OrderResult(success=False, comment="Failed to get tick for close")

        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        filling = mt5.ORDER_FILLING_IOC
        if sym_info.filling_mode & mt5.ORDER_FILLING_FOK:
            filling = mt5.ORDER_FILLING_FOK

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "price": close_price,
            "deviation": 25,
            "magic": pos.magic,
            "comment": "CLOSE_BY_BOT",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            app_logger.info(f"Position {ticket} CLOSED successfully @ {res.price}")
            return OrderResult(
                success=True,
                ticket=res.order,
                volume=res.volume,
                price=res.price,
                retcode=res.retcode,
                comment=res.comment,
                magic_number=pos.magic,
            )

        return OrderResult(
            success=False,
            retcode=res.retcode if res else -1,
            comment=res.comment if res else "Unknown error",
        )


# Backward compatibility alias
MT5AdapterReadOnly = MT5Adapter
