"""Telegram Alert — Enhanced Alerts + Hourly Summary + Start Command.

Sends alerts to Telegram on BET HIGHER / BET LOWER decisions.
Uses raw HTTPS POST (no polling machinery needed for v1).
Includes inline keyboard "View Signal" button for deep-linking.
Hourly 15m summary sent at the top of each hour.
/start command responds with a welcome message.
"""
import os
import json
import time
import logging
import threading
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


# ── Hourly 15m Summary ────────────────────────────────────────────────

def get_signals_for_hour(client, hour_start_ms: int, hour_end_ms: int, duration: str = "15m") -> list[dict]:
    """Query signals within a time range for hourly summary.

    Args:
        client: Supabase client
        hour_start_ms: hour start in epoch milliseconds
        hour_end_ms: hour end in epoch milliseconds
        duration: filter by duration (default "15m")
    """
    result = (
        client.table("signals")
        .select(
            "id, final_decision, market_window_start, market_duration, "
            "strike_price, current_price, odds, score, edge_pct, "
            "paper_trades!signal_id(resolved_outcome, simulated_pnl)"
        )
        .eq("mode", "safe")
        .eq("market_duration", duration)
        .gte("market_window_start", hour_start_ms)
        .lt("market_window_start", hour_end_ms)
        .order("market_window_start", desc=False)
        .execute()
    )
    signals = result.data
    for sig in signals:
        trades = sig.pop("paper_trades", [])
        if trades:
            sig["resolved_outcome"] = trades[0].get("resolved_outcome")
            sig["simulated_pnl"] = trades[0].get("simulated_pnl")
        else:
            sig["resolved_outcome"] = None
            sig["simulated_pnl"] = None
    return signals


def format_hourly_summary(signals: list[dict], hour_start_ms: int) -> str:
    """Format an hourly summary of 15m signals.

    Args:
        signals: list of signal dicts with resolution data
        hour_start_ms: start of the hour in epoch ms
    """
    hour_dt = datetime.fromtimestamp(hour_start_ms / 1000, tz=ET)
    hour_label = hour_dt.strftime("%-I:%M %p")

    bet_signals = [s for s in signals if s["final_decision"] in ("BET HIGHER", "BET LOWER")]
    total = len(signals)

    if not bet_signals:
        lines = [
            f"<b>\U0001f4ca 15m Hourly — {hour_label} ET</b>",
            "",
            f"  {total} window{'s' if total != 1 else ''} analyzed",
            "  No BET signals this hour",
        ]
        return "\n".join(lines)

    # Count wins/losses
    won = [s for s in bet_signals if s.get("resolved_outcome") and s["resolved_outcome"] in ("UP", "DOWN")]
    wins = [s for s in won if (s["final_decision"] == "BET HIGHER" and s["resolved_outcome"] == "UP")
            or (s["final_decision"] == "BET LOWER" and s["resolved_outcome"] == "DOWN")]
    losses = [s for s in won if s not in wins]
    unresolved = [s for s in bet_signals if not s.get("resolved_outcome")]
    pnl = sum(s.get("simulated_pnl", 0) or 0 for s in won)

    win_count = len(wins)
    loss_count = len(losses)
    pending = len(unresolved)

    result_parts = []
    if win_count:
        result_parts.append(f"\u2705 {win_count} won")
    if loss_count:
        result_parts.append(f"\u274c {loss_count} lost")
    if pending:
        result_parts.append(f"\u23f3 {pending} pending")
    result_str = "  \u00b7  ".join(result_parts)

    pnl_str = f"${pnl:+.2f}" if pnl != 0 else "$0.00"
    pnl_icon = "\U0001f4b2" if pnl > 0 else "\U0001f4b9" if pnl < 0 else "\u2014"

    lines = [
        f"<b>\U0001f4ca 15m Hourly — {hour_label} ET</b>",
        "",
        f"  {len(bet_signals)} BET signal{'s' if len(bet_signals) != 1 else ''}  \u00b7  {result_str}",
        f"  {pnl_icon} P&amp;L: {pnl_str}",
    ]

    return "\n".join(lines)


def send_hourly_summary(client) -> bool:
    """Send hourly 15m summary if we're at the top of the hour.

    Called from the heartbeat. Queries the previous hour's 15m signals
    and sends a summary message.
    """
    now = datetime.now(ET)
    if now.minute > 5:
        return False

    # Previous hour boundaries
    prev_hour = now - timedelta(hours=1)
    hour_start = prev_hour.replace(minute=0, second=0, microsecond=0)
    hour_end = hour_start + timedelta(hours=1)
    hour_start_ms = int(hour_start.timestamp() * 1000)
    hour_end_ms = int(hour_end.timestamp() * 1000)

    try:
        signals = get_signals_for_hour(client, hour_start_ms, hour_end_ms, duration="15m")
        message = format_hourly_summary(signals, hour_start_ms)
        return send_telegram_alert(message)
    except Exception as e:
        log.warning(f"Hourly summary failed: {e}")
        return False


# ── Start Command / Welcome Message ────────────────────────────────────

WELCOME_MESSAGE = (
    "<b>Welcome to Verge Signals</b> \U0001f3af\n"
    "\n"
    "Verge tracks BTC Up/Down binary markets on Polymarket and sends you\n"
    "alerts when a BET signal is detected.\n"
    "\n"
    "<b>What you'll receive:</n"
    "\u2022 Real-time BET alerts</b> — instant notification when a signal fires,\n"
    "  with price, odds, indicators, and a link to the full signal detail.\n"
    "\n"
    "<b>\u2022 Hourly 15m summary</b> — a recap of the previous hour's 15-minute\n"
    "  market activity: how many BET signals, wins/losses, and P&amp;L.\n"
    "\n"
    "<b>\u2022 Weekly digest</b> — overall performance, win rate, and graduation\n"
    "  gate status.\n"
    "\n"
    "Dashboard: <a href=\"{url}\">{url}</a>\n"
    "\n"
    "<i>Sent by Verge \u00b7 A bet filter with a memory</i>"
).format(url=DASHBOARD_URL)


def start_bot_listener() -> None:
    """Start a background thread polling for /start commands.

    Uses getUpdates with long-polling (timeout=30s) to receive messages.
    Only responds to /start with the welcome message.
    """
    if not TELEGRAM_BOT_TOKEN:
        log.warning("Telegram credentials not configured, bot listener not started")
        return

    def _poll_loop():
        offset = None
        log.info("Telegram bot listener started (polling for /start)")
        while True:
            try:
                params = {"timeout": 30}
                if offset is not None:
                    params["offset"] = offset
                resp = requests.get(
                    f"{TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                    params=params,
                    timeout=40,
                )
                resp.raise_for_status()
                data = resp.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if text == "/start" and chat_id == TELEGRAM_CHAT_ID:
                        log.info("Received /start, sending welcome message")
                        send_telegram_alert(WELCOME_MESSAGE)
            except requests.exceptions.RequestException as e:
                log.warning(f"Bot listener poll failed: {e}")
                time.sleep(5)
            except Exception as e:
                log.warning(f"Bot listener error: {e}")
                time.sleep(5)

    thread = threading.Thread(target=_poll_loop, daemon=True, name="telegram-bot-listener")
    thread.start()


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
