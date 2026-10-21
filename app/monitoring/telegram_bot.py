"""
Telegram Notification and Remote Monitoring Bot.
Dispatches real-time alerts for executions, risk vetoes, circuit trips,
and daily P&L summaries, with graceful offline degradation.
"""
from datetime import datetime, timezone
from typing import Optional
import requests

from app.config import settings
from app.core.schemas import OrderResult
from app.monitoring.logger import app_logger


class TelegramNotifier:
    """Sends structured Markdown alerts to configured Telegram chat."""

    def __init__(
        self,
        bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN,
        chat_id: Optional[str] = settings.TELEGRAM_CHAT_ID,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.is_configured = bool(self.bot_token and self.chat_id)
        if not self.is_configured:
            app_logger.info("Telegram Notifier: No token configured. Alerts will log to console only.")

    def send_message(self, text: str) -> bool:
        """Send message via Telegram Bot API with graceful degradation."""
        if not self.is_configured:
            # Fallback to local logger
            app_logger.info(f"[TELEGRAM SIMULATED]: {text.strip()}")
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=8)
            if resp.status_code == 200:
                return True
            app_logger.warning(f"Telegram API error {resp.status_code}: {resp.text}")
        except Exception as e:
            app_logger.warning(f"Failed to send Telegram message: {e}")

        return False

    def notify_order_opened(self, order: OrderResult, sl: float, tp: float):
        """Send notification when a trade is executed."""
        msg = (
            f"🔔 *ORDER EXECUTED*\n"
            f"• Ticket: `{order.ticket}`\n"
            f"• Action: `{order.comment}`\n"
            f"• Volume: `{order.volume}` Lot\n"
            f"• Entry Price: `{order.price}`\n"
            f"• Hard SL: `{sl}`\n"
            f"• Hard TP: `{tp}`\n"
            f"• Magic: `{order.magic_number}`\n"
            f"• Time: `{order.timestamp_utc.strftime('%H:%M:%S UTC')}`"
        )
        self.send_message(msg)

    def notify_order_closed(self, ticket: int, profit: float, reason: str):
        """Send notification when a position is closed."""
        icon = "🟢" if profit >= 0 else "🔴"
        msg = (
            f"{icon} *POSITION CLOSED*\n"
            f"• Ticket: `{ticket}`\n"
            f"• Net Profit: `${profit:+,.2f}`\n"
            f"• Reason: `{reason}`\n"
            f"• Time: `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        self.send_message(msg)

    def notify_risk_veto(self, decision: str, reason: str):
        """Send notification when Risk Manager vetoes a trade."""
        msg = (
            f"⚠️ *TRADE VETOED BY RISK MANAGER*\n"
            f"• Decision: `{decision}`\n"
            f"• Reason: _{reason}_\n"
            f"• Time: `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        self.send_message(msg)

    def notify_circuit_breaker(self, state: str, reason: str):
        """Send high-priority alert when Circuit Breaker trips."""
        msg = (
            f"🚨 *CIRCUIT BREAKER ALERT*\n"
            f"• State: `{state}`\n"
            f"• Reason: *{reason}*\n"
            f"• Action: Liquidating or Pausing\n"
            f"• Time: `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        self.send_message(msg)

    def notify_daily_summary(
        self,
        balance: float,
        daily_pnl: float,
        trades_count: int,
        win_rate: float,
        max_dd: float,
    ):
        """Send end-of-day summary report."""
        icon = "📈" if daily_pnl >= 0 else "📉"
        msg = (
            f"{icon} *DAILY PERFORMANCE REPORT*\n"
            f"• Current Balance: `${balance:,.2f}`\n"
            f"• Daily Net PnL: `${daily_pnl:+,.2f}`\n"
            f"• Total Trades: `{trades_count}`\n"
            f"• Win Rate: `{win_rate:.1f}%`\n"
            f"• Max Drawdown: `{max_dd:.2f}%`\n"
            f"• Time: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`"
        )
        self.send_message(msg)
