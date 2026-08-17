# BUILD_PLAN: Polymarket RTDS Integration

## Context

Verge's current 15m price collection is fragile: the heartbeat opens a short-lived WebSocket, grabs one Chainlink tick, and closes. The persistent accumulator thread (`_accumulate_ticks`) tries to maintain a connection, but free hosting tiers kill long-lived connections, causing sparse tick data and unreliable TWAP computation.

The FrondEnt PolymarketBTC15mAssistant project demonstrates a better architecture: a persistent WebSocket connection to Polymarket's RTDS (Real-Time Data Stream) endpoint, with exponential-backoff reconnection and on-chain fallback.

**Goal:** Replace Verge's short-lived WebSocket pattern with a persistent RTDS service that continuously writes Chainlink ticks to `price_snapshots`, making the TWAP computation reliable.

**What we're borrowing:** Architecture only (persistent WS, reconnection, source separation). NOT the TA engine (RSI, MACD, VWAP, Heikin Ashi) — Verge already has its own indicators.

---

## Phase 1: Create `polymarket_rtds.py` — Persistent RTDS Service

**File:** `backend/polymarket_rtds.py` (NEW)

This module manages a persistent WebSocket connection to Polymarket's live data feed, writing every Chainlink BTC tick to `price_snapshots`.

### Key design decisions:
- **Threading model:** Use a daemon thread (same pattern as `start_ws_tick_accumulator`), not asyncio. Verge is a synchronous Flask app — asyncio in a thread caused the "no current event loop" bug we already fixed.
- **Reconnection:** Exponential backoff 2s → 15s cap (matching existing accumulator pattern).
- **Ping:** Send `"PING"` every 5 seconds (Polymarket requires this).
- **Source label:** `source="polymarket_rtds"` (distinct from old `polymarket_ws_tick`).
- **Tick write:** Use existing `db.write_price_snapshot_sync()`.

### Functions:

```python
# backend/polymarket_rtds.py

RTDS_URL = "wss://ws-live-data.polymarket.com"
RTDS_PING_INTERVAL = 5  # seconds
RECONNECT_BASE = 2      # seconds
RECONNECT_MAX = 15      # seconds

def start_rtds_thread() -> threading.Thread:
    """Start the persistent RTDS connection as a daemon thread.
    Called once at startup from app.py (same pattern as start_ws_tick_accumulator).
    """

def _rtds_loop():
    """Main loop: connect, subscribe, read ticks, reconnect on failure.
    Runs in a daemon thread with exponential backoff.
    """

def _connect_and_read():
    """Connect to RTDS, subscribe to crypto_prices_chainlink, read BTC ticks.
    Sends PING every 5 seconds. Returns when connection closes.
    """

def _handle_tick(raw_message: str) -> None:
    """Parse RTDS message, extract BTC price, write to price_snapshots.
    Flexible parsing: tries multiple field names (value, price, current, data).
    Handles timestamp in both seconds and milliseconds.
    """

def _send_ping(ws) -> None:
    """Send PING to keep connection alive. Called every 5 seconds."""
```

### Subscribe format (from FrondEnt + Polymarket docs):
```json
{
  "action": "subscribe",
  "subscriptions": [{
    "topic": "crypto_prices_chainlink",
    "type": "*",
    "filters": ""
  }]
}
```

### Message parsing (defensive, multiple field names):
```python
def _handle_tick(raw_message):
    msg = json.loads(raw_message)
    if msg.get("topic") != "crypto_prices_chainlink":
        return

    payload = msg.get("payload") or {}
    symbol = str(payload.get("symbol", "")).lower()
    if "btc" not in symbol:
        return

    price = payload.get("value") or payload.get("price") or payload.get("current")
    if price is None:
        return

    timestamp_ms = _parse_timestamp(payload.get("timestamp") or payload.get("updatedAt"))

    db.write_price_snapshot_sync(
        source="polymarket_rtds",
        symbol="BTCUSD",
        price=float(price),
        timestamp_ms=timestamp_ms,
    )
```

---

## Phase 2: Wire RTDS into startup, replace old accumulator

**File:** `backend/app.py`

### Changes:
1. Import `start_rtds_thread` from `polymarket_rtds`
2. Replace `start_ws_tick_accumulator()` call with `start_rtds_thread()` at startup
3. Remove `record_polymarket_ws_tick()` from the heartbeat (no longer needed — RTDS is always running)
4. Keep `record_price_tick("15m")` for on-chain Chainlink ticks (separate source)

### Before:
```python
if os.environ.get("WEB_CONCURRENCY", "1") == "1":
    start_bot_listener()
    start_ws_tick_accumulator()
```

### After:
```python
if os.environ.get("WEB_CONCURRENCY", "1") == "1":
    start_bot_listener()
    start_rtds_thread()
```

### In heartbeat, remove:
```python
# DELETE this block from heartbeat():
try:
    record_polymarket_ws_tick()
except Exception as e:
    log.warning(f"Polymarket WS tick failed (non-fatal): {e}")
```

---

## Phase 3: Update price resolution chain

**File:** `backend/engine.py`

### Changes to `_generate_signal_inner()`:
1. Update the TWAP source to read from `polymarket_rtds` instead of `polymarket_ws_tick`
2. Keep the fallback chain intact

### In the TWAP section (around line 544):
```python
# BEFORE:
recent_ticks = _db.get_recent_price_snapshots(
    _db.get_client(), source="polymarket_ws_tick", symbol="BTCUSD",
    since_ms=now_ms_val - 90_000,
)

# AFTER:
recent_ticks = _db.get_recent_price_snapshots(
    _db.get_client(), source="polymarket_rtds", symbol="BTCUSD",
    since_ms=now_ms_val - 90_000,
)
```

### In `get_current_price_data_for_duration()` (around line 359):
```python
# BEFORE:
raw_ticks = db.get_price_snapshots(
    client, source="chainlink", symbol="BTC",
    since_ms=since_ms, limit=500,
)

# AFTER: Try RTDS first, fall back to chainlink
raw_ticks = db.get_price_snapshots(
    client, source="polymarket_rtds", symbol="BTCUSD",
    since_ms=since_ms, limit=500,
)
if len(raw_ticks) < 10:
    raw_ticks = db.get_price_snapshots(
        client, source="chainlink", symbol="BTC",
        since_ms=since_ms, limit=500,
    )
```

### In `_resolve_via_chainlink_ticks()`:
```python
# BEFORE:
ticks = db.get_price_snapshots(
    client, source="chainlink", symbol="BTC",
    since_ms=window_start, limit=1000,
)

# AFTER: Try RTDS first, fall back to chainlink
ticks = db.get_price_snapshots(
    client, source="polymarket_rtds", symbol="BTCUSD",
    since_ms=window_start, limit=1000,
)
if len(ticks) < 2:
    ticks = db.get_price_snapshots(
        client, source="chainlink", symbol="BTC",
        since_ms=window_start, limit=1000,
    )
```

---

## Phase 4: Update diagnostics

**File:** `backend/app.py`

### Changes to `api_diagnostics()`:
1. Update TWAP query to use `polymarket_rtds` source
2. Add RTDS-specific diagnostics (tick count, last update time, connection status)

### In diagnostics endpoint:
```python
# BEFORE:
twap_ticks = db.get_recent_price_snapshots(client, "polymarket_ws_tick", "BTCUSD",
                                            since_ms=now_ms - 90_000)

# AFTER:
twap_ticks = db.get_recent_price_snapshots(client, "polymarket_rtds", "BTCUSD",
                                            since_ms=now_ms - 90_000)
```

### Add RTDS health check to response:
```python
# Count RTDS ticks in last 5 minutes
rtds_ticks_5m = db.get_recent_price_snapshots(
    client, "polymarket_rtds", "BTCUSD",
    since_ms=now_ms - 300_000
)

live["rtds_tick_count_5m"] = len(rtds_ticks_5m)
live["rtds_last_tick_age_ms"] = (
    now_ms - rtds_ticks_5m[-1].timestamp_ms
    if rtds_ticks_5m else None
)
```

---

## Phase 5: Update diagnostics frontend

**File:** `frontend/index.html`

### Changes:
1. Update "Live Prices" labels to reflect new source names
2. Add RTDS health indicator

### In the live prices section:
```javascript
// BEFORE:
const liveLabels = {'polymarket_ws_twap_60s': 'TWAP 60s', 'chainlink_onchain': 'Chainlink', ...};

// AFTER:
const liveLabels = {'polymarket_ws_twap_60s': 'Chainlink RTDS', 'chainlink_onchain': 'Chainlink On-Chain', ...};
```

### Add RTDS health badge:
```javascript
if (live.rtds_tick_count_5m !== undefined) {
    const age = live.rtds_last_tick_age_ms;
    const healthClass = age !== null && age < 10000 ? 'ok' : 'warn';
    html += `<div class="diag-kv-item">
        <div class="diag-kv-label">RTDS Health</div>
        <div class="diag-kv-value ${healthClass}">${live.rtds_tick_count_5m} ticks / 5m</div>
    </div>`;
}
```

---

## Phase 6: Deprecate old accumulator

**File:** `backend/engine.py`

### Changes:
1. Mark `start_ws_tick_accumulator()` as deprecated (keep for reference, don't call)
2. Mark `record_polymarket_ws_tick()` as deprecated
3. Update source label from `polymarket_ws_tick` to `polymarket_rtds` in all references

### Keep old functions but add deprecation comments:
```python
def start_ws_tick_accumulator() -> None:
    """DEPRECATED: Use polymarket_rtds.start_rtds_thread() instead.
    Kept for reference. Do not call from app.py.
    """
    ...

def record_polymarket_ws_tick() -> bool:
    """DEPRECATED: RTDS handles continuous tick collection.
    Kept for reference. Do not call from heartbeat.
    """
    ...
```

---

## Files Modified

| File | Change |
|------|--------|
| `backend/polymarket_rtds.py` | NEW — persistent RTDS WebSocket service |
| `backend/app.py` | Replace `start_ws_tick_accumulator` → `start_rtds_thread`, remove `record_polymarket_ws_tick` from heartbeat, update diagnostics |
| `backend/engine.py` | Update TWAP source to `polymarket_rtds`, update resolution source, deprecate old functions |
| `frontend/index.html` | Update source labels, add RTDS health indicator |

## Files NOT Modified

| File | Reason |
|------|--------|
| `backend/chainlink_fetcher.py` | Keep as-is — on-chain fallback is valuable |
| `backend/data_fetcher.py` | Keep as-is — `get_polymarket_chainlink_price()` still useful for one-off queries |
| `backend/market_config.py` | No changes needed |
| `backend/db.py` | No schema changes — `price_snapshots` already has `source` column |

---

## Validation

1. **Start RTDS thread:** Verify it connects and starts writing ticks immediately
2. **Check tick density:** After 5 minutes, verify `polymarket_rtds` ticks in `price_snapshots` (>50 ticks expected)
3. **Verify TWAP:** Check that `polymarket_ws_twap_60s` in diagnostics shows a real value (not "—")
4. **Verify source breakdown:** Diagnostics should show `polymarket_rtds` as the primary source
5. **Verify signal generation:** 15m signals should use RTDS ticks for TWAP and resolution
6. **Run tests:** `backend\venv\Scripts\python.exe -m pytest backend\tests\ -q`
7. **Monitor reconnection:** Kill the WS connection and verify it reconnects within 15 seconds
