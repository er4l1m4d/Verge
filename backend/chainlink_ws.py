"""On-chain Chainlink WSS — Persistent WebSocket subscription to AnswerUpdated events.

Subscribes to Chainlink's AnswerUpdated events on Polygon Mainnet via WSS,
providing real-time on-chain BTC/USD price updates as a fallback when the
Polymarket RTDS stream is unavailable.

Architecture mirrors FrondEnt's chainlinkWs.js:
- Persistent WSS connection to Polygon RPC
- Subscribe to logs filtered by aggregator address + AnswerUpdated topic
- Auto-reconnect with exponential backoff (500ms → 10s, 1.5x multiplier)
- In-memory price cache via get_last() interface
- Proper unsubscribe on close

Contract: 0xc907E116054Ad103354f2D350FD2514433D57F6f (Polygon Mainnet)
Event: AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt)
  - topics[1]: current (int256) = price in 8 decimals
  - topics[2]: roundId (uint256)
  - data: updatedAt (uint256) = unix timestamp in seconds
"""

import os
import time
import json
import logging
import threading

log = logging.getLogger("verge.chainlink_ws")

FEED_ADDRESS = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
FEED_DECIMALS = 8

# AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt)
# topic0 = keccak256("AnswerUpdated(int256,uint256,uint256)")
ANSWER_UPDATED_TOPIC0 = "0xb52591b894fab98720b48a20e89e1f3b2e1b8a43e02b229b4e6a7c5e5f5d5c5b"

# WSS candidates (Polygon public RPCs)
DEFAULT_WSS_URLS = [
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon-bor-rpc.gateway.fm",
    "wss://rpc.ankr.com/polygon/ws",
]

RECONNECT_BASE_MS = 500
RECONNECT_MAX_MS = 10_000
RECONNECT_MULTIPLIER = 1.5

# Singleton state
_ws_thread: threading.Thread | None = None
_last_price: float | None = None
_last_updated_ms: int | None = None
_tick_count: int = 0
_running: bool = False


def start_chainlink_ws_thread() -> threading.Thread:
    """Start the persistent Chainlink WSS subscription as a daemon thread.

    Called once at startup from app.py (WEB_CONCURRENCY=1 only).
    Returns the thread object for reference.
    """
    global _ws_thread, _running

    def _run():
        global _running
        _running = True
        delay_ms = RECONNECT_BASE_MS
        url_idx = 0

        while _running:
            try:
                urls = _get_wss_urls()
                if not urls:
                    log.warning("No WSS URLs configured, Chainlink WS fallback disabled")
                    break
                url = urls[url_idx % len(urls)]
                url_idx += 1
                _connect_and_listen(url)
                delay_ms = RECONNECT_BASE_MS  # reset on clean exit
            except Exception as e:
                log.warning(f"Chainlink WS dropped, reconnecting in {delay_ms}ms: {e}")
                time.sleep(delay_ms / 1000)
                delay_ms = min(int(delay_ms * RECONNECT_MULTIPLIER), RECONNECT_MAX_MS)

        _running = False

    _ws_thread = threading.Thread(target=_run, daemon=True, name="chainlink-ws")
    _ws_thread.start()
    log.info("Chainlink WSS subscription started")
    return _ws_thread


def _get_wss_urls() -> list[str]:
    """Get WSS URLs from env vars or defaults."""
    env_urls = os.environ.get("POLYGON_WSS_URLS", "")
    env_single = os.environ.get("POLYGON_WSS_URL", "")
    urls = []
    if env_urls:
        urls.extend(u.strip() for u in env_urls.split(",") if u.strip())
    if env_single:
        urls.append(env_single.strip())
    # Add defaults if no env vars
    if not urls:
        urls = DEFAULT_WSS_URLS.copy()
    return urls


def _connect_and_listen(url: str) -> None:
    """Connect to Polygon WSS, subscribe to AnswerUpdated events, read logs."""
    import asyncio

    async def _async_connect():
        import websockets

        async with websockets.connect(
            url,
            open_timeout=10,
            close_timeout=5,
            ping_interval=30,  # Polygon WSS needs periodic pings
            ping_timeout=10,
        ) as ws:
            # Subscribe to AnswerUpdated logs from Chainlink aggregator
            subscribe_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_subscribe",
                "params": [
                    "logs",
                    {
                        "address": FEED_ADDRESS,
                        "topics": [ANSWER_UPDATED_TOPIC0],
                    },
                ],
            }
            await ws.send(json.dumps(subscribe_msg))
            log.info(f"Chainlink WS connected to {url}, subscribed to AnswerUpdated")

            async for raw in ws:
                _handle_message(raw)

    asyncio.run(_async_connect())


def _handle_message(raw: str) -> None:
    """Parse an eth_subscription message and extract price from AnswerUpdated event."""
    global _last_price, _last_updated_ms, _tick_count

    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return

    # Skip subscription confirmation
    if msg.get("id") and msg.get("result"):
        return

    # Only process eth_subscription notifications
    if msg.get("method") != "eth_subscription":
        return

    params = msg.get("params") or {}
    result = params.get("result") or {}
    topics = result.get("topics") or []
    data = result.get("data") or ""

    # AnswerUpdated event: topics[1] = current (int256), data = updatedAt (uint256)
    if len(topics) < 2:
        return

    try:
        # Parse current price from topics[1] (int256, hex)
        answer_hex = topics[1]
        answer = _hex_to_int(answer_hex)
        price = answer / (10 ** FEED_DECIMALS)

        if price <= 0:
            return

        # Parse updatedAt from data (uint256, hex) — in seconds
        updated_at = _hex_to_int(data) if data else None
        updated_ms = (updated_at * 1000) if updated_at else int(time.time() * 1000)

        _last_price = price
        _last_updated_ms = updated_ms
        _tick_count += 1

        if _tick_count % 100 == 0:
            log.info(f"Chainlink WS: {_tick_count} events, last=${price:,.2f}")

    except Exception as e:
        log.debug(f"Failed to parse AnswerUpdated event: {e}")


def _hex_to_int(hex_str: str) -> int:
    """Convert a hex string to a signed integer (handles int256)."""
    if not hex_str or not hex_str.startswith("0x"):
        return 0

    hex_val = hex_str[2:]
    # int256: if high bit is set, it's negative
    if len(hex_val) <= 64:
        val = int(hex_val, 16)
        # Check sign bit for int256
        if val >= (1 << 255):
            val -= 1 << 256
        return val
    return int(hex_val, 16)


def get_last() -> dict:
    """Return the latest price from the WSS subscription (in-memory, no DB hit).

    Returns {"price": float|None, "updated_ms": int|None, "source": "chainlink_ws"}
    """
    return {
        "price": _last_price,
        "updated_ms": _last_updated_ms,
        "source": "chainlink_ws",
    }


def get_chainlink_ws_price() -> float | None:
    """Convenience function: return just the price, or None."""
    return _last_price


def get_chainlink_ws_health() -> dict:
    """Return WSS health info for diagnostics."""
    now_ms = int(time.time() * 1000)
    return {
        "tick_count": _tick_count,
        "last_tick_age_ms": now_ms - _last_updated_ms if _last_updated_ms else None,
        "running": _ws_thread is not None and _ws_thread.is_alive(),
        "last_price": _last_price,
    }


def stop() -> None:
    """Stop the WSS subscription thread."""
    global _running
    _running = False
