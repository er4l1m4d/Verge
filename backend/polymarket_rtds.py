"""Polymarket RTDS (Real-Time Data Stream) — Persistent Chainlink Price Feed.

Maintains a persistent WebSocket connection to Polymarket's live data feed,
writing every BTC tick to both an in-memory ring buffer and the price_snapshots
table. This replaces the old short-lived WebSocket pattern with a robust,
always-on connection.

Architecture:
  - Ring buffer (in-memory) for low-latency reads during signal generation
  - DB writes for durable historical record
  - Daemon thread runs the main reconnection loop
  - asyncio.run() manages the WebSocket event loop (thread-safe for Flask)
  - Exponential backoff on connection failure (500ms → 10s, 1.5x)
  - PING keepalive every 5 seconds
  - Defensive message parsing (multiple field name variants)
"""

import os
import time
import json
import logging
import threading
import uuid
from collections import deque

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
_connection_count: int = 0
_reconnect_count: int = 0
_malformed_count: int = 0
_db_write_failures: int = 0
_duplicate_count: int = 0
_out_of_order_count: int = 0
_largest_gap_ms: int = 0
_last_connection_id: str | None = None
_seen_observations: set[tuple[int, float]] = set()


class TickRingBuffer:
    """Thread-safe in-memory ring buffer for recent RTDS ticks.

    Avoids DB round-trip on every heartbeat. DB remains the durable
    record for historical analysis; the ring buffer is the low-latency
    read path for live signal generation.
    """

    def __init__(self, max_age_ms: int = 120_000):
        self._ticks: deque = deque()
        self._lock = threading.Lock()
        self._max_age_ms = max_age_ms

    def append(self, price: float, timestamp_ms: int) -> None:
        with self._lock:
            self._ticks.append((price, timestamp_ms))
            self._evict()

    def recent(self, since_ms: int) -> list[tuple[float, int]]:
        with self._lock:
            self._evict()
            return [(p, ts) for p, ts in self._ticks if ts >= since_ms]

    def _evict(self) -> None:
        cutoff = int(time.time() * 1000) - self._max_age_ms
        while self._ticks and self._ticks[0][1] < cutoff:
            self._ticks.popleft()


# Singleton — 120s window covers 90s TWAP lookback with margin
_rtds_buffer = TickRingBuffer(max_age_ms=120_000)


def get_rtds_ticks(since_ms: int) -> list[dict]:
    """Get recent RTDS ticks from ring buffer (no DB hit).

    Returns list of dicts compatible with compute_twap():
    [{"price": float, "timestamp_ms": int}, ...]
    """
    return [{"price": p, "timestamp_ms": ts} for p, ts in _rtds_buffer.recent(since_ms)]


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
                global _reconnect_count
                _reconnect_count += 1
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
        global _connection_count, _last_connection_id

        async with websockets.connect(
            RTDS_URL,
            open_timeout=10,
            close_timeout=5,
            ping_interval=None,  # we handle PING ourselves
            ping_timeout=None,
        ) as ws:
            _connection_count += 1
            _last_connection_id = str(uuid.uuid4())
            # Subscribe to Chainlink BTC price feed
            await ws.send(json.dumps({
                "action": "subscribe",
                "subscriptions": [{
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": "{\"symbol\":\"btc/usd\"}",
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
    """Parse an RTDS message, extract BTC price, write to ring buffer + DB.

    Ring buffer is the fast read path for live signal generation.
    DB is the durable record for historical analysis.
    Defensive parsing: tries multiple field names for both price and timestamp
    since the Polymarket WS schema may change.
    """
    global _last_tick_ms, _tick_count, _malformed_count, _db_write_failures
    global _duplicate_count, _out_of_order_count, _largest_gap_ms

    try:
        msg = json.loads(raw_message)
    except (json.JSONDecodeError, ValueError):
        _malformed_count += 1
        return

    # Only process Chainlink BTC price messages
    if msg.get("topic") != "crypto_prices_chainlink":
        return

    payload = msg.get("payload") or {}
    if not isinstance(payload, dict):
        _malformed_count += 1
        return

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
        _malformed_count += 1
        return

    try:
        price = float(price)
    except (TypeError, ValueError):
        _malformed_count += 1
        return

    if price <= 0:
        _malformed_count += 1
        return

    # Flexible timestamp extraction
    ts_raw = (
        payload.get("timestamp")
        or payload.get("updatedAt")
        or payload.get("ts")
    )
    ts_ms = _parse_timestamp(ts_raw)
    observation_key = (ts_ms, price)
    if observation_key in _seen_observations:
        _duplicate_count += 1
        return
    _seen_observations.add(observation_key)
    if len(_seen_observations) > 5000:
        _seen_observations.clear()

    if _last_tick_ms and ts_ms < _last_tick_ms:
        _out_of_order_count += 1
        return
    if _last_tick_ms:
        _largest_gap_ms = max(_largest_gap_ms, ts_ms - _last_tick_ms)

    # Write to ring buffer (fast, in-memory) then DB (durable record)
    import db
    try:
        _rtds_buffer.append(price, ts_ms)
        db.write_price_snapshot_sync(
            source="rtds_chainlink",
            symbol="BTCUSD",
            price=price,
            timestamp_ms=ts_ms,
        )
        _last_tick_ms = ts_ms
        _tick_count += 1

        if _tick_count % 100 == 0:
            log.info(f"RTDS: {_tick_count} ticks written, last=${price:,.2f}")

    except Exception as e:
        _db_write_failures += 1
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
        "connection_count": _connection_count,
        "reconnect_count": _reconnect_count,
        "connection_id": _last_connection_id,
        "malformed_count": _malformed_count,
        "db_write_failures": _db_write_failures,
        "duplicate_count": _duplicate_count,
        "out_of_order_count": _out_of_order_count,
        "largest_gap_ms": _largest_gap_ms,
    }
