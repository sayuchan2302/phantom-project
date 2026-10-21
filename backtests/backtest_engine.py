"""
Backtest Engine with Realistic Friction Modeling.
Simulates trades with dynamic spread, execution slippage, commission,
and strict avoidance of look-ahead bias (signals generated at t, executed at open of t+1).
"""
from datetime import datetime
from typing import Callable, List, Optional, Tuple
import numpy as np
import pandas as pd

from app.core.enums import SignalType
from app.core.schemas import BacktestReport, BacktestTrade, BarData, TradeSignal
from app.monitoring.logger import app_logger


class BacktestEngine:
    """Event-driven bar-by-bar backtester with realistic trading costs."""

    def __init__(
        self,
        initial_balance_usd: float = 1000.0,
        lot_size: float = 0.01,
        commission_per_lot_usd: float = 3.5,
        base_slippage_pips: float = 0.8,
        contract_size: float = 100.0,
    ):
        self.initial_balance = initial_balance_usd
        self.lot_size = lot_size
        self.commission_per_lot = commission_per_lot_usd
        self.base_slippage_pips = base_slippage_pips
        self.contract_size = contract_size

    def calculate_dynamic_spread(self, bar: BarData) -> float:
        """
        Estimate spread in points:
        Night hours (21:00 - 23:00 UTC) have higher spread.
        """
        base_spread_pts = bar.spread if bar.spread > 0 else 22
        hour = bar.time_utc.hour
        if 20 <= hour <= 23:
            return base_spread_pts * 1.5
        return float(base_spread_pts)

    def run_backtest(
        self,
        strategy_name: str,
        bars: List[BarData],
        signal_generator: Callable[[int, List[BarData]], Optional[TradeSignal]],
    ) -> Tuple[BacktestReport, List[BacktestTrade]]:
        """
        Run simulation across chronological bars.
        signal_generator(i, bars) evaluates up to bar i (NO LOOK-AHEAD),
        trade is entered at open of bar i+1.
        """
        if len(bars) < 20:
            raise ValueError(f"Insufficient bars for backtest: {len(bars)}")

        trades: List[BacktestTrade] = []
        equity = self.initial_balance
        peak_equity = self.initial_balance
        max_drawdown_usd = 0.0
        max_drawdown_pct = 0.0

        i = 10
        total_bars = len(bars)

        while i < total_bars - 1:
            curr_bar = bars[i]

            # Signal evaluated at close of curr_bar (index i)
            sig = signal_generator(i, bars)

            if sig and sig.signal_type in [SignalType.BUY, SignalType.SELL]:
                next_bar = bars[i + 1]
                entry_time = next_bar.time_utc
                raw_entry_price = next_bar.open

                # Apply dynamic spread & slippage at entry
                spread_pts = self.calculate_dynamic_spread(next_bar)
                spread_cost_usd = (spread_pts * 0.001 * self.contract_size) * self.lot_size
                slippage_dist = (self.base_slippage_pips * 0.1)
                slippage_cost_usd = (slippage_dist * self.contract_size) * self.lot_size
                commission_usd = self.commission_per_lot * self.lot_size

                if sig.signal_type == SignalType.BUY:
                    entry_price = raw_entry_price + (spread_pts * 0.001 / 2.0) + slippage_dist
                    sl = sig.suggested_sl
                    tp = sig.suggested_tp
                else:
                    entry_price = raw_entry_price - (spread_pts * 0.001 / 2.0) - slippage_dist
                    sl = sig.suggested_sl
                    tp = sig.suggested_tp

                # Simulate forward bars until TP, SL or max hold (40 bars)
                trade_closed = False
                hold_bar_idx = i + 1

                while hold_bar_idx < total_bars and not trade_closed:
                    h_bar = bars[hold_bar_idx]

                    if sig.signal_type == SignalType.BUY:
                        # Check SL first (conservative)
                        if h_bar.low <= sl:
                            exit_price = sl - slippage_dist
                            exit_reason = "SL"
                            trade_closed = True
                        elif h_bar.high >= tp:
                            exit_price = tp
                            exit_reason = "TP"
                            trade_closed = True
                    else:  # SELL
                        if h_bar.high >= sl:
                            exit_price = sl + slippage_dist
                            exit_reason = "SL"
                            trade_closed = True
                        elif h_bar.low <= tp:
                            exit_price = tp
                            exit_reason = "TP"
                            trade_closed = True

                    # Timeout exit after holding 40 bars (e.g. 10 hours on M15)
                    if not trade_closed and (hold_bar_idx - (i + 1)) >= 40:
                        exit_price = h_bar.close
                        exit_reason = "TIME_EXIT"
                        trade_closed = True

                    if trade_closed:
                        exit_time = h_bar.time_utc
                        if sig.signal_type == SignalType.BUY:
                            price_diff = exit_price - entry_price
                        else:
                            price_diff = entry_price - exit_price

                        gross_pnl = price_diff * self.contract_size * self.lot_size
                        total_costs = spread_cost_usd + slippage_cost_usd + commission_usd
                        net_pnl = gross_pnl - total_costs

                        equity += net_pnl
                        peak_equity = max(peak_equity, equity)
                        dd_usd = peak_equity - equity
                        dd_pct = (dd_usd / peak_equity) * 100.0 if peak_equity > 0 else 0.0

                        max_drawdown_usd = max(max_drawdown_usd, dd_usd)
                        max_drawdown_pct = max(max_drawdown_pct, dd_pct)

                        trades.append(
                            BacktestTrade(
                                symbol=sig.symbol,
                                entry_time_utc=entry_time,
                                exit_time_utc=exit_time,
                                signal_type=sig.signal_type,
                                entry_price=round(entry_price, 3),
                                exit_price=round(exit_price, 3),
                                sl=round(sl, 3),
                                tp=round(tp, 3),
                                lot_size=self.lot_size,
                                gross_pnl_usd=round(gross_pnl, 2),
                                spread_cost_usd=round(spread_cost_usd, 2),
                                slippage_cost_usd=round(slippage_cost_usd, 2),
                                commission_usd=round(commission_usd, 2),
                                net_pnl_usd=round(net_pnl, 2),
                                exit_reason=exit_reason,
                            )
                        )
                        # Advance outer pointer to after this trade closes
                        i = hold_bar_idx
                        break

                    hold_bar_idx += 1

            i += 1

        # Calculate summary statistics
        total_trades = len(trades)
        if total_trades == 0:
            return BacktestReport(
                strategy_name=strategy_name,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                profit_factor=0.0,
                max_drawdown_usd=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                total_net_profit_usd=0.0,
                avg_trade_pnl=0.0,
            ), []

        wins = [t.net_pnl_usd for t in trades if t.net_pnl_usd > 0]
        losses = [abs(t.net_pnl_usd) for t in trades if t.net_pnl_usd < 0]
        gross_win_sum = sum(wins)
        gross_loss_sum = sum(losses)

        win_rate = (len(wins) / total_trades) * 100.0
        profit_factor = (gross_win_sum / gross_loss_sum) if gross_loss_sum > 0 else (99.0 if gross_win_sum > 0 else 1.0)
        total_net_profit = sum(t.net_pnl_usd for t in trades)
        avg_trade = total_net_profit / total_trades

        # Annualized Sharpe ratio approximation
        returns = [t.net_pnl_usd for t in trades]
        std_ret = np.std(returns) if len(returns) > 1 else 1.0
        mean_ret = np.mean(returns) if len(returns) > 0 else 0.0
        sharpe = (mean_ret / std_ret * np.sqrt(250)) if std_ret > 0 else 0.0

        report = BacktestReport(
            strategy_name=strategy_name,
            total_trades=total_trades,
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(win_rate, 2),
            profit_factor=round(min(profit_factor, 99.0), 2),
            max_drawdown_usd=round(max_drawdown_usd, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            sharpe_ratio=round(sharpe, 2),
            total_net_profit_usd=round(total_net_profit, 2),
            avg_trade_pnl=round(avg_trade, 2),
        )

        app_logger.info(
            f"Backtest [{strategy_name}]: {total_trades} trades, WinRate={report.win_rate}%, "
            f"PF={report.profit_factor}, NetPnL=${report.total_net_profit_usd:,.2f}, MaxDD={report.max_drawdown_pct}%"
        )
        return report, trades
