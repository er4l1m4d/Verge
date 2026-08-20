# BUILD_PLAN: Semantic Hardening — Naming, Latency, Validation

## Context

Three architectural issues identified:

1. **Naming:** `polymarket_ws_twap_60s` implies official status. It's a local estimate.
2. **Latency:** RTDS writes to DB, then reads back. DB should be durable record, not message bus.
3. **Validation:** Price-to-beat semantics not empirically verified against Polymarket settlement.

---

## Issue 1: Naming — Add `reference_status` and rename TWAP label

### Current state

- `polymarket_ws_twap_60s` used in 4 files, 9 locations
- `reference_status` exists only in build plan doc — NOT in any table, dataclass, or API
- No distinction between "estimated" vs "official" price

### Plan

**Migration 012: Add `reference_status` to signals table**

```sql
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reference_status TEXT DEFAULT 'estimated';
```

Values: `estimated` | `fallback` | `official`

**Rename in code:**

| File | Line | Before | After |
|------|------|--------|-------|
| `engine.py` | 572 | `"polymarket_ws_twap_60s"` | `"polymarket_rtds_60s_twap_estimate"` |
| `engine.py` | 570 | `"same source Polymarket resolves with"` | `"local TWAP estimate from RTDS Chainlink"` |
| `app.py` | 821 | `"polymarket_ws_twap_60s"` | `"polymarket_rtds_60s_twap_estimate"` |
| `app.py` | 856 | `live["polymarket_ws_twap_60s"]` | `live["polymarket_rtds_60s_twap_estimate"]` |
| `app.py` | 870 | `live.get("polymarket_ws_twap_60s")` | `live.get("polymarket_rtds_60s_twap_estimate")` |
| `index.html` | 3660 | `'polymarket_ws_twap_60s'` | `'polymarket_rtds_60s_twap_estimate'` |
| `index.html` | 3661 | label `'Chainlink RTDS'` | label `'RTDS TWAP (est.)'` |
| `index.html` | 3664 | `k === 'polymarket_ws_twap_60s'` | `k === 'polymarket_rtds_60s_twap_estimate'` |

**Set `reference_status` in `persist_signal()`:**

```python
reference_status = "estimated" if price_source == "polymarket_rtds_60s_twap_estimate" else "fallback"
```

**Update `SignalRow` dataclass** in db.py: add `reference_status: str = "estimated"`

**Update `write_signal()`** in db.py: include `reference_status` in insert

---

## Issue 2: RTDS → In-Memory Ring Buffer → TWAP

### Current flow

```
RTDS WS → _handle_tick() → db.write_price_snapshot_sync() → Supabase INSERT
                                                                    ↓
Signal generation → db.get_recent_price_snapshots() → compute_twap() → current_price
```

Problem: DB round-trip on every heartbeat. DB is durable record, not message bus.

### Target flow

```
RTDS WS → _handle_tick() → ring_buffer.append() → DB INSERT (async, non-blocking)
                                    ↓
Signal generation → ring_buffer.recent(90s) → compute_twap() → current_price
```

### Plan

**Add ring buffer to `polymarket_rtds.py`:**

```python
from collections import deque
import threading

class TickRingBuffer:
    """Thread-safe in-memory ring buffer for recent ticks."""

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

# Singleton
_rtds_buffer = TickRingBuffer(max_age_ms=120_000)
```

**Update `_handle_tick()`:**

```python
def _handle_tick(raw_message: str) -> None:
    # ... parse price, timestamp_ms ...
    _rtds_buffer.append(price, ts_ms)  # in-memory (fast)
    db.write_price_snapshot_sync(...)   # DB (async durable record)
```

**Add public accessor:**

```python
def get_rtds_ticks(since_ms: int) -> list[dict]:
    """Get recent RTDS ticks from ring buffer (no DB hit)."""
    return [{"price": p, "timestamp_ms": ts} for p, ts in _rtds_buffer.recent(since_ms)]
```

**Update TWAP computation in `engine.py`:**

```python
# BEFORE:
recent_ticks = _db.get_recent_price_snapshots(
    _db.get_client(), source="polymarket_rtds", symbol="BTCUSD",
    since_ms=now_ms_val - 90_000,
)

# AFTER: Read from ring buffer (in-memory, no DB hit)
from polymarket_rtds import get_rtds_ticks
raw_ticks = get_rtds_ticks(since_ms=now_ms_val - 90_000)
recent_ticks = [PriceSnapshotRow(source="polymarket_rtds", symbol="BTCUSD",
                                  price=p, timestamp_ms=ts) for p, ts in raw_ticks]
```

**Same update for 15m candle bars** in `get_current_price_data_for_duration()`:

```python
# BEFORE:
raw_ticks = db.get_price_snapshots(client, source="polymarket_rtds", ...)

# AFTER:
from polymarket_rtds import get_rtds_ticks
raw_ticks = get_rtds_ticks(since_ms=since_ms)
```

DB fallback remains for when ring buffer is cold (e.g., just started).

---

## Issue 3: Price-to-Beat Empirical Validation

### What we know

| Duration | `price_to_beat` source | Status |
|----------|----------------------|--------|
| 1h | Gamma API `eventMetadata.priceToBeat` | Official, present |
| 15m | Computed: first Chainlink tick at window start | **Not verified** |

### What we don't know

For 15m, Polymarket's actual resolution reference could be:
- Chainlink RTDS observation at T0 (what we assume)
- Chainlink Data Streams TWAP 60s (paid product, different from on-chain)
- Some other internal reference

### Validation plan

**Add a resolution audit table** (migration 013):

```sql
CREATE TABLE IF NOT EXISTS resolution_audit (
    id BIGSERIAL PRIMARY KEY,
    window_start BIGINT NOT NULL,
    duration TEXT NOT NULL,
    local_strike_source TEXT,          -- what we used as strike
    local_strike_price DOUBLE PRECISION,
    official_outcome TEXT,             -- Polymarket's verdict
    local_outcome TEXT,                -- our verdict using local strike
    agreement BOOLEAN,                -- do they match?
    strike_method TEXT,                -- "chainlink_bar_open", "coinbase_fallback", etc.
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Populate during resolution** in `_resolve_via_chainlink_ticks()`:

After computing outcome, log the strike method used:
```python
strike_method = "rtds_chainlink_tick"  # or "onchain_chainlink_tick", "coinbase", "binance"
```

**Empirical test:**

1. Collect 100+ resolved 15m windows
2. Compare `local_outcome` vs `official_outcome` by strike method
3. If agreement > 99%: our assumption is validated
4. If agreement < 99%: investigate which strike method Polymarket actually uses

**This is a long-running validation, not a code change.** The audit table captures the data; analysis happens offline.

---

## Files Modified

| File | Change |
|------|--------|
| `backend/polymarket_rtds.py` | Add `TickRingBuffer`, `get_rtds_ticks()`, update `_handle_tick()` |
| `backend/engine.py` | Read TWAP from ring buffer, rename price_source, update comments |
| `backend/db.py` | Add `reference_status` to `SignalRow`, `write_signal()`, migration 012, 013 |
| `backend/app.py` | Rename live prices keys, add resolution audit writes |
| `frontend/index.html` | Update labels, add reference_status display |

## Validation

1. **Naming:** `price_source` in signals table shows `polymarket_rtds_60s_twap_estimate`
2. **Ring buffer:** `get_rtds_ticks()` returns ticks without DB query
3. **DB still written:** `price_snapshots` table still gets RTDS ticks (durable record)
4. **Resolution audit:** `resolution_audit` table populates during heartbeat
5. **Run tests:** `backend\venv\Scripts\python.exe -m pytest backend\tests\ -q`
