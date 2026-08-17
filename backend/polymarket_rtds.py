"""Polymarket RTDS (Real-Time Data Stream) — Persistent Chainlink Price Feed.

Maintains a persistent WebSocket connection to Polymarket's live data feed,
writing every BTC tick to the price_snapshots table. This replaces the old
short-lived WebSocket pattern with a robust, always-on connection.

Polymarket's RTDS endpoint:
  wss://ws-live-data.polymarket.com
  Topic: crypto_prices_chainlink
  No authentication required.
  Must send PING every 5 seconds.

Architecture:
  - Daemon thread runs the main reconnection loop
  - asyncio.run() manages the WebSocket event loop (thread-safe for Flask)
  - Exponential backoff on connection failure (2s → 15s cap)
  - PING keepalive every 5 seconds
  - Defensive message parsing (multiple field name variants)
"""

import os
import time
import json
import logging
import threading

log = logging.getLogger("verge.rtds")

RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5  # seconds — Polymarket requires PING every 5s
RECONNECT_BASE_MS = 500  # initial reconnect delay (ms) — matches FrondEnt
RECONNECT_MAX_MS = 10_000  # max reconnect delay (ms)
RECONNECT_MULTIPLIER = 1.5  # exponential backoff multiplier

# Thread reference for health checks
_rtds_thread: threading.Thread | None = None
_last_tick_ms: int = 0
_tick_count: int = 0


def start_rtds_thread() -> threading.Thread:
    """Start the persistent RTDS connection as a daemon thread.

    Called once at startup from app.py (WEB_CONCURRENCY=1 only).
    Returns the thread object for reference.
    """
    global _rtds_thread

    def _run():
        delay_ms = RECONNECT_BASE_MS
        while True:
            try:
                _connect_and_read()
                delay_ms = RECONNECT_BASE_MS  # reset on clean exit
            except Exception as e:
                log.warning(f"RTDS dropped, reconnecting in {delay_ms}ms: {e}")
                time.sleep(delay_ms / 1000)
                delay_ms = min(int(delay_ms * RECONNECT_MULTIPLIER), RECONNECT_MAX_MS)

    _rtds_thread = threading.Thread(target=_run, daemon=True, name="rtds-stream")
    _rtds_thread.start()
    log.info("RTDS persistent stream started")
    return _rtds_thread


def _connect_and_read():
    """Connect to RTDS, subscribe, read BTC ticks, send PING keepalive.

    Runs synchronously using asyncio.run(). Returns when connection closes
    (cleanly or on error), allowing the caller to reconnect.
    """
    import asyncio

    async def _async_connect():
        import websockets

        async with websockets.connect(
            RTDS_URL,
            open_timeout=10,
            close_timeout=5,
            ping_interval=None,  # we handle PING ourselves
            ping_timeout=None,
        ) as ws:
            # Subscribe to Chainlink BTC price feed
            await ws.send(json.dumps({
                "action": "subscribe",
                "subscriptions": [{
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": "",
                }],
            }))
            log.info("RTDS connected, subscribed to crypto_prices_chainlink")

            # Start PING keepalive task
            ping_task = asyncio.create_task(_ping_loop(ws))

            try:
                async for raw in ws:
                    _handle_tick(raw)
            finally:
                ping_task.cancel()
                try:
                    await ping_task
                except asyncio.CancelledError:
                    pass

    asyncio.run(_async_connect())


async def _ping_loop(ws):
    """Send PING to Polymarket every 5 seconds to keep the connection alive."""
    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            await ws.send("PING")
        except Exception:
            break  # connection is dead, exit ping loop


def _handle_tick(raw_message: str) -> None:
    """Parse an RTDS message, extract BTC price, write to price_snapshots.

    Defensive parsing: tries multiple field names for both price and timestamp
    since the Polymarket WS schema may change.
    """
    global _last_tick_ms, _tick_count

    try:
        msg = json.loads(raw_message)
    except (json.JSONDecodeError, ValueError):
        return

    # Only process Chainlink BTC price messages
    if msg.get("topic") != "crypto_prices_chainlink":
        return

    payload = msg.get("payload") or {}

    # Flexible symbol extraction
    symbol = str(
        payload.get("symbol")
        or payload.get("pair")
        or payload.get("ticker")
        or ""
    ).lower()
    if "btc" not in symbol:
        return

    # Flexible price extraction
    price = (
        payload.get("value")
        or payload.get("price")
        or payload.get("current")
        or payload.get("data")
    )
    if price is None:
        return

    try:
        price = float(price)
    except (TypeError, ValueError):
        return

    if price <= 0:
        return

    # Flexible timestamp extraction
    ts_raw = (
        payload.get("timestamp")
        or payload.get("updatedAt")
        or payload.get("ts")
    )
    ts_ms = _parse_timestamp(ts_raw)

    # Write to database
    import db
    try:
        db.write_price_snapshot_sync(
            source="polymarket_rtds",
            symbol="BTCUSD",
            price=price,
            timestamp_ms=ts_ms,
        )
        _last_tick_ms = ts_ms
        _tick_count += 1

        if _tick_count % 100 == 0:
            log.info(f"RTDS: {_tick_count} ticks written, last=${price:,.2f}")

    except Exception as e:
        log.warning(f"RTDS: failed to write tick: {e}")


def _parse_timestamp(raw) -> int:
    """Parse a timestamp from various formats into milliseconds.

    Handles:
      - Unix seconds (float or int)
      - Unix milliseconds (float or int)
      - String timestamps
      - None (falls back to current time)
    """
    if raw is None:
        return int(time.time() * 1000)

    try:
        val = float(raw)
    except (TypeError, ValueError):
        return int(time.time() * 1000)

    # Heuristic: if value < 1e12, it's seconds, not milliseconds
    if val < 1e12:
        return int(val * 1000)
    return int(val)


def get_rtds_health() -> dict:
    """Return RTDS health info for diagnostics."""
    now_ms = int(time.time() * 1000)
    return {
        "tick_count": _tick_count,
        "last_tick_age_ms": now_ms - _last_tick_ms if _last_tick_ms > 0 else None,
        "running": _rtds_thread is not None and _rtds_thread.is_alive(),
    }
