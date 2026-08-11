"""Telegram Alert — Phase 6.2 / 6.3 + Enhanced Alerts.

Sends alerts to Telegram on BET HIGHER / BET LOWER decisions.
Uses raw HTTPS POST (no polling machinery needed for v1).
Includes inline keyboard "View Signal" button for deep-linking.
"""
import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger("verge.telegram")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org"
DASHBOARD_URL = "https://vergesignals.vercel.app"

ET = timezone(timedelta(hours=-4))


def send_telegram_alert(message: str, signal_id: int | None = None) -> bool:
    """Send a message to Telegram, optionally with an inline keyboard.

    Returns True on success, False on failure.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not configured, skipping alert")
        return False

    url = f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    if signal_id is not None:
        payload["reply_markup"] = json.dumps({
            "inline_keyboard": [[{
                "text": "View Signal",
                "url": f"{DASHBOARD_URL}/#signal/{signal_id}",
            }]]
        })

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Telegram alert sent successfully")
        return True
    except requests.exceptions.RequestException as e:
        log.warning(f"Telegram alert failed: {e}")
        return False


def format_signal_alert(sig) -> str:
    """Format a LiveSignal into a Telegram message.

    Only called for BET HIGHER / BET LOWER (never SKIP).
    Includes time window, price context, and all indicator details.
    """
    direction_icon = "\U0001f4c8" if sig.final_decision == "BET HIGHER" else "\U0001f4c9"
    duration_label = "15M" if getattr(sig, "duration", "1h") == "15m" else "1H"
    market_label = f"BTC Up/Down {duration_label}"

    window_start_ms = getattr(sig, "hour_open_time", None)
    duration_ms = 900000 if getattr(sig, "duration", "1h") == "15m" else 3600000

    if window_start_ms:
        start_dt = datetime.fromtimestamp(window_start_ms / 1000, tz=ET)
        end_dt = start_dt + timedelta(milliseconds=duration_ms)
        day_str = start_dt.strftime("%b %-d")
        time_range = f"{start_dt.strftime('%-I:%M %p')} \u2013 {end_dt.strftime('%-I:%M %p')} ET"
        window_line = f"{day_str}, {time_range}"
    else:
        window_line = "Unknown window"

    strike = getattr(sig, "strike_price", None)
    current = getattr(sig, "current_price", None)
    strike_str = f"${strike:,.2f}" if strike else "\u2014"
    current_str = f"${current:,.2f}" if current else "\u2014"

    lines = [
        f"<b>{direction_icon} {sig.final_decision}</b>  \u00b7  {duration_label}",
        "",
        f"<b>Market:</b> {market_label}",
        f"<b>Window:</b> {window_line}",
        "",
        f"<b>Price to Beat:</b> {strike_str}",
        f"<b>Current Price:</b> {current_str}",
        f"<b>Odds:</b> {sig.odds:.0%}",
        "",
        f"<b>Confidence:</b> {sig.confidence}",
        f"<b>Score:</b> {sig.score:+.2f}",
        f"<b>Edge:</b> {sig.edge_pct:.1f}%",
        "",
        f"<b>RSI:</b> {sig.rsi:.1f}",
        f"<b>MA:</b> {'▲' if sig.ma_signal > 0 else '▼' if sig.ma_signal < 0 else '—'}",
        f"<b>Volume:</b> {'▲' if sig.volume_signal > 0 else '▼' if sig.volume_signal < 0 else '—'}",
    ]

    if sig.fee_eroded:
        lines.append("")
        lines.append("\u26a0\ufe0f <i>Fee-eroded signal</i>")

    return "\n".join(lines)


def alert_on_signal(sig, signal_id: int | None = None) -> None:
    """Send Telegram alert if signal is a BET (not SKIP).

    Called from the heartbeat endpoint.
    """
    if sig.final_decision in ("BET HIGHER", "BET LOWER"):
        message = format_signal_alert(sig)
        send_telegram_alert(message, signal_id=signal_id)


def send_weekly_digest(stats: dict) -> bool:
    """Send a weekly digest with performance stats.

    Args:
        stats: from get_stats(client)
    """
    lines = [
        "<b>\U0001f4ca Weekly Digest \u2014 Verge</b>",
        "",
        f"  Trades: {stats.get('resolved', 0)} resolved",
        f"  Win Rate: {stats.get('win_rate', 0)}%",
        f"  ROI: {stats.get('roi_pct', 0)}%",
        f"  P&amp;L: ${stats.get('cumulative_pnl', 0):+.2f}",
    ]

    if stats.get("unlock_real_orders"):
        lines.extend(["", "\u2705 <b>Graduation gate: UNLOCKED</b>"])
    else:
        lines.append("")
        lines.append(f"\U0001f512 Graduation: {stats.get('total_trades', 0)}/200 trades")

    return send_telegram_alert("\n".join(lines))
