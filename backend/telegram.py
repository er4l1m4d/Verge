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
    Includes duration label for multi-market disambiguation (Phase 9.2).
    """
    direction_icon = "📈" if sig.final_decision == "BET HIGHER" else "📉"
    duration_label = "15M" if getattr(sig, "duration", "1h") == "15m" else "1H"

    lines = [
        f"<b>{direction_icon} {sig.final_decision}</b>  ·  {duration_label}",
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


def send_weekly_digest(safe_stats: dict, risk_stats: dict, comparison: dict) -> bool:
    """Send a weekly digest comparing safe-mode vs risk-mode performance.

    Args:
        safe_stats: from get_stats(client, mode="safe")
        risk_stats: from get_stats(client, mode="risk")
        comparison: from get_comparison_stats(client)
    """
    safe = comparison.get("safe", {})
    risk_all = comparison.get("risk_all", {})
    risk_ctrl = comparison.get("risk_controlled", {})

    lines = [
        "<b>📊 Weekly Digest — Verge</b>",
        "",
        "<b>🛡️ Safe Mode (Filtered)</b>",
        f"  Trades: {safe.get('resolved', 0)} resolved",
        f"  Win Rate: {safe.get('win_rate', 0)}%",
        f"  ROI: {safe.get('roi_pct', 0)}%",
        f"  P&L: ${safe.get('cumulative_pnl', 0):+.2f}",
        "",
        "<b>🎲 Risk Mode (Forced Bets)</b>",
        f"  Trades: {risk_all.get('resolved', 0)} resolved",
        f"  Win Rate: {risk_all.get('win_rate', 0)}%",
        f"  ROI: {risk_all.get('roi_pct', 0)}%",
        f"  P&L: ${risk_all.get('cumulative_pnl', 0):+.2f}",
    ]

    if risk_ctrl.get("resolved", 0) > 0:
        lines.extend([
            "",
            "<b>⏱️ Same Period Comparison</b>",
            f"  Risk (same window): {risk_ctrl.get('win_rate', 0)}% WR, {risk_ctrl.get('roi_pct', 0)}% ROI",
        ])

    if safe_stats.get("unlock_real_orders"):
        lines.extend(["", "✅ <b>Graduation gate: UNLOCKED</b>"])
    else:
        lines.append("")
        lines.append(f"🔒 Graduation: {safe_stats.get('total_trades', 0)}/200 safe trades")

    return send_telegram_alert("\n".join(lines))
