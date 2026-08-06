"""Telegram Alert — Phase 6.2 / 6.3.

Sends alerts to Telegram on BET HIGHER / BET LOWER decisions.
Uses raw HTTPS POST (no polling machinery needed for v1).
"""
import os
import logging
import requests

log = logging.getLogger("verge.telegram")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org"


def send_telegram_alert(message: str) -> bool:
    """Send a message to Telegram.

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
    """
    direction_icon = "📈" if sig.final_decision == "BET HIGHER" else "📉"

    lines = [
        f"<b>{direction_icon} {sig.final_decision}</b>",
        "",
        f"<b>Confidence:</b> {sig.confidence}",
        f"<b>Score:</b> {sig.score:+.2f}",
        f"<b>Edge:</b> {sig.edge_pct:.1f}%",
        f"<b>Odds:</b> {sig.odds:.0%}",
        f"<b>Suggested Price:</b> {sig.suggested_price}",
        "",
        f"<b>RSI:</b> {sig.rsi:.1f}",
        f"<b>MA:</b> {'▲' if sig.ma_signal > 0 else '▼' if sig.ma_signal < 0 else '—'}",
        f"<b>Volume:</b> {'▲' if sig.volume_signal > 0 else '▼' if sig.volume_signal < 0 else '—'}",
        "",
        f"<b>Market:</b> {sig.market_slug}",
        f"<b>Minutes left:</b> {sig.minutes_remaining}",
    ]

    if sig.fee_eroded:
        lines.append("")
        lines.append("⚠️ <i>Fee-eroded signal</i>")

    return "\n".join(lines)


def alert_on_signal(sig) -> None:
    """Send Telegram alert if signal is a BET (not SKIP).

    Called from the heartbeat endpoint.
    """
    if sig.final_decision in ("BET HIGHER", "BET LOWER"):
        message = format_signal_alert(sig)
        send_telegram_alert(message)
