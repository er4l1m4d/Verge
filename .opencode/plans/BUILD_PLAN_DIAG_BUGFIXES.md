# BUILD_PLAN: Diagnostics Page Bug Fixes (3 issues)

## Context

From the diagnostics page screenshot (Aug 16, ~04:xx ET):
- TWAP 60s shows "—" but Last Tick shows $63,037.47 → the `>= 3` tick gate is failing by 1-2 ticks
- Source Breakdown count is 1,000 → Supabase's hard cap, not the true total
- 15m Polymarket Strike shows "—" → `get_polymarket_live_market()` has no Chainlink fallback for 15m

## Phase 1: Lower TWAP tick threshold from 3 to 2

**Root cause**: `compute_twap()` at `engine.py:1040` already handles 1-2 ticks correctly (returns the last tick's price when there's only 1 tick, or a proper weighted average for 2). The callers gate on `>= 3`, blocking TWAP when only 1-2 ticks exist in the 90-second window.

**Files**: `backend/app.py:821`, `backend/engine.py:548`

**Change**: `len(ticks) >= 3` → `len(ticks) >= 2` in both locations.

Rationale: 2 ticks produce a real time-weighted average. 1 tick produces the tick's own price as TWAP (correct but not smoothed). Both are better than showing "—".

```python
# app.py:821
"polymarket_ws_twap_60s": compute_twap(twap_ticks, now_ms) if len(twap_ticks) >= 2 else None,

# engine.py:548
twap_price = compute_twap(recent_ticks, window_end_ms=now_ms_val) if len(recent_ticks) >= 2 else None
```

## Phase 2: Paginate Source Breakdown query

**Root cause**: `get_source_breakdown()` at `db.py:1030` queries with no `.limit()` or `.range()`, hitting Supabase's 1,000-row hard cap. At ~1 tick/second, 1,000 rows covers only ~17 minutes of the 24-hour window.

**File**: `backend/db.py:1030-1046`

**Change**: Add `.range()`-based pagination loop.

```python
def get_source_breakdown(client: Client, since_ms: int) -> list[dict]:
    """Get average price and count per source for the last 24h."""
    PAGE_SIZE = 1000
    offset = 0
    by_source: dict[str, list[float]] = {}

    while True:
        resp = (
            client.table("price_snapshots")
            .select("source,price")
            .gte("timestamp_ms", since_ms)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        for r in rows:
            src = r["source"]
            by_source.setdefault(src, []).append(float(r["price"]))
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return [
        {"source": src, "avg_price": round(sum(prices) / len(prices), 2), "count": len(prices)}
        for src, prices in sorted(by_source.items(), key=lambda x: -len(x[1]))
    ]
```

## Phase 3: Add Chainlink fallback for 15m strike in diagnostics

**Root cause**: `get_polymarket_live_market()` reads `eventMetadata.priceToBeat`, which doesn't exist for 15m. The diagnostics table shows "—" for 15m's Polymarket Strike column, even though the Recent Signals table shows correct strikes ($63,195.59, $63,186.93) from Chainlink bars.

**File**: `backend/polymarket_fetcher.py:188-240` (function `get_polymarket_live_market`)

**Change**: After the Gamma API query, if `price_to_beat` is `None`, compute it from Chainlink tick data (same approach as the engine's fallback).

```python
# After the existing return dict is built, before returning:
if price_to_beat is None:
    # 15m markets don't have eventMetadata.priceToBeat —
    # compute from Chainlink ticks at window start
    try:
        from datetime import datetime, timezone
        start_dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
        window_start_ms = int(start_dt.timestamp() * 1000)
        import db
        client = db.get_client()
        ticks = db.get_price_snapshots(
            client, source="chainlink", symbol="BTC",
            since_ms=window_start_ms - 5_000, limit=10,
        )
        window_ticks = [t for t in ticks if t["timestamp_ms"] >= window_start_ms]
        if not window_ticks:
            window_ticks = ticks  # fallback to nearest tick
        if window_ticks:
            price_to_beat = float(window_ticks[0]["price"])
    except Exception:
        pass
```

## Files Modified

| File | Change |
|------|--------|
| `backend/app.py:821` | `>= 3` → `>= 2` |
| `backend/engine.py:548` | `>= 3` → `>= 2` |
| `backend/db.py:1030-1046` | Paginate `get_source_breakdown()` with `.range()` |
| `backend/polymarket_fetcher.py:188-240` | Add Chainlink tick fallback for 15m `price_to_beat` |

## Validation

1. Run tests: `backend\venv\Scripts\python.exe -m pytest backend\tests\ -q`
2. Verify `compute_twap` with 1 and 2 ticks works
3. Verify `get_source_breakdown` returns correct counts > 1,000
4. Verify `get_polymarket_live_market("btc-up-or-down-15m")` returns `price_to_beat` from Chainlink ticks
