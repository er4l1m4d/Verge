# Build Plan: Bug Fixes — Resolution Accuracy + TWAP Accumulator

Two confirmed bugs from diagnostics review.

---

## Fix 1: Resolution Accuracy 0% (SKIP counting)

**File:** `backend/db.py`, function `get_resolution_accuracy_by_source()` (line ~1081)

**Bug:** SKIP decisions count toward `total` but can never satisfy agreement conditions, making accuracy mathematically 0% when most signals are SKIP.

**Fix:** Add early continue for non-directional decisions:

```python
for sig in signals:
    if sig["final_decision"] not in ("BET HIGHER", "BET LOWER"):
        continue  # SKIP has no direction to be right or wrong about
    key = (sig["market_duration"], sig["market_window_start"])
    actual = outcomes.get(key)
    if actual is None:
        continue
    # ... rest unchanged
```

**Also:** In `frontend/index.html`, update the diagnostics page default filter to hide SKIPs:
- Change `diagDurationFilter` default logic to also filter out SKIP in `renderDiagSignals()`
- Add a filter toggle: All / BET only / SKIP only (default: BET only)

---

## Fix 2: TWAP Accumulator Not Writing (Option C: Both)

**Root cause:** Persistent WebSocket thread likely killed by Render's free tier infrastructure.

**Fix:** Keep accumulator thread (works on paid tier), add heartbeat tick write as reliable fallback.

### 2.1 — Add `record_polymarket_ws_tick()` to `engine.py`

New function that fetches Polymarket price via the short-lived WS (already works in `get_polymarket_chainlink_price()`) and writes it as a tick:

```python
def record_polymarket_ws_tick() -> bool:
    """Fetch Polymarket WS price and write as a tick (heartbeat fallback for accumulator)."""
    import asyncio
    from data_fetcher import get_polymarket_chainlink_price
    import db

    try:
        result = asyncio.get_event_loop().run_until_complete(
            get_polymarket_chainlink_price(timeout_s=4.0)
        )
        if result:
            price, ts = result
            ts_ms = ts if ts else int(time.time() * 1000)
            db.write_price_snapshot_sync(
                source="polymarket_ws_tick", symbol="BTCUSD",
                price=float(price), timestamp_ms=ts_ms,
            )
            return True
    except Exception as e:
        log.debug(f"Polymarket WS tick record failed (non-fatal): {e}")
    return False
```

### 2.2 — Call from heartbeat in `app.py`

After the existing `record_price_tick()` call (line ~354), add:

```python
# Record Polymarket WS tick (fallback for accumulator)
try:
    record_polymarket_ws_tick()
except Exception as e:
    log.debug(f"Polymarket WS tick failed for {dur} (non-fatal): {e}")
```

### 2.3 — Add logging to accumulator failure path

Already exists at `engine.py:1018` — `log.warning(f"WS tick accumulator dropped, reconnecting: {e}")`. No change needed.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/db.py` | Exclude SKIP from `get_resolution_accuracy_by_source()` |
| `backend/engine.py` | Add `record_polymarket_ws_tick()` |
| `backend/app.py` | Call `record_polymarket_ws_tick()` in heartbeat |
| `frontend/index.html` | Default to BET-only filter in diagnostics Recent Signals |
