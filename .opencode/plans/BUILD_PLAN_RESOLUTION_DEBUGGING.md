# Build Plan: Resolution Debugging + Fix

Addresses the issues identified in the Claude conversation:
1. Only coinbase_spot shows up in diagnostics
2. price_snapshots has zero writes
3. condition_id might be empty for 15m markets
4. get_polymarket_resolution() silently fails

## Strategy

Follow Claude's recommendation: **instrument every step first**, deploy, wait for logs, then fix the actual cause. Don't guess again.

---

## Phase 1 — Add Comprehensive Logging

**Goal:** Make every step of the resolution chain and price recording visible in logs.

### 1.1 — Log conditionId extraction in get_current_market() (engine.py)

At line ~213 where `condition_id` is extracted, add logging:

```python
cid = market.get("conditionId")
if not cid:
    log.warning(f"Market missing conditionId: question={market.get('question','')[:60]}")
best = {
    ...
    "condition_id": cid,
    ...
}
```

### 1.2 — Instrument get_polymarket_resolution() (polymarket_fetcher.py)

Replace all 4 silent early returns with logged warnings:

```python
def get_polymarket_resolution(condition_id: str) -> dict | None:
    try:
        resp = requests.get(f"{GAMMA_BASE}/markets/{condition_id}", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("closed"):
            log.warning(f"PM resolution: market {condition_id} not closed yet")
            return None
        prices_raw = data.get("outcomePrices")
        outcomes_raw = data.get("outcomes")
        if not prices_raw or not outcomes_raw:
            log.warning(f"PM resolution: missing prices/outcomes for {condition_id} — keys: {list(data.keys())[:10]}")
            return None
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        if not prices or not outcomes or len(prices) < 2 or len(outcomes) < 2:
            log.warning(f"PM resolution: short arrays for {condition_id} — prices={prices} outcomes={outcomes}")
            return None
        winner_idx = 0 if float(prices[0]) > 0.9 else 1
        outcome = outcomes[winner_idx].upper()
        log.info(f"PM resolution: {condition_id} -> {outcome}")
        return {"outcome": outcome, "closed_time": data.get("closedTime"), "outcome_prices": prices}
    except Exception as e:
        log.warning(f"PM resolution: request failed for {condition_id}: {e}")
        return None
```

### 1.3 — Log resolution chain gates in resolve_previous_hour() (engine.py)

Add logging at each gate:

- Before the condition_id query (line ~848): log the window_start being checked
- After the query (line ~855): log whether condition_id was found
- Before calling get_polymarket_resolution(): log the condition_id
- After calling it: log the result

### 1.4 — Upgrade tick recording logs (engine.py)

- `record_polymarket_ws_tick()`: change `log.debug` to `log.warning` on failure (line ~785)
- `record_price_tick()`: ensure failure is logged at WARNING level

---

## Phase 2 — Fix the TWAP/Price Source Chain

**Goal:** Ensure price_snapshots accumulates ticks so TWAP works.

### 2.1 — Add logging to write_price_snapshot_sync() (db.py)

The function silently swallows exceptions. Add a log on failure:

```python
except Exception as e:
    log.warning(f"write_price_snapshot_sync failed: {e}")
    return None
```

(This already exists at db.py line 357-358 — verify it's at WARNING level, not DEBUG.)

### 2.2 — Verify record_polymarket_ws_tick() is actually called

The heartbeat calls it for all durations (app.py line 362). But the import at the top of app.py must include it. Verify.

### 2.3 — If WebSocket fails on Render, add periodic fallback

If the WS connection doesn't survive on Render's free tier, the heartbeat tick write (every 5 minutes) should still work via `get_polymarket_chainlink_price()` (short-lived WS). Verify this function actually succeeds by checking logs after deploy.

---

## Phase 3 — Fix Phase 2 Progress Counter

**Goal:** Change the readiness counter to count only windows with official_outcome, not raw resolved windows.

### 3.1 — Update GET /api/phase2-progress (app.py)

Change the query to count windows with `official_outcome IS NOT NULL` instead of all resolved windows:

```python
# Current: counts all resolved windows
resp = client.table("window_outcomes").select("*", count="exact")...

# New: only count windows with Polymarket-validated outcomes
resp = client.table("window_outcomes").select("*", count="exact") \
    .not_.is_("official_outcome", "null")...
```

### 3.2 — Reset progress tracking

The 433 accumulated windows were resolved against Coinbase fallback, not Polymarket truth. The counter should only count from now (once official_outcome starts populating).

---

## Files Changed

| File | Changes |
|------|---------|
| `engine.py` | Log conditionId extraction, log resolution chain gates, upgrade tick recording logs |
| `polymarket_fetcher.py` | Instrument all 4 silent early returns in get_polymarket_resolution() |
| `db.py` | Verify write_price_snapshot_sync logging is at WARNING level |
| `app.py` | Update phase2-progress to count official_outcome, verify imports |

## What to Do After Deploy

1. Wait for next 1-2 15m windows to close
2. Check Render logs for:
   - `"Market missing conditionId"` — tells us if 15m markets have conditionId
   - `"PM resolution: ... not closed yet"` / `"missing prices/outcomes"` / `"short arrays"` — tells us which silent return is hit
   - `"PM resolution: ... -> UP/DOWN"` — tells us it's working
   - `"PM resolution: request failed"` — tells us the API call itself fails
   - `"write_price_snapshot_sync failed"` — tells us if ticks are being written
3. Paste the log output and we fix the actual cause
