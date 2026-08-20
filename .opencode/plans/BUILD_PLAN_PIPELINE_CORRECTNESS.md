# BUILD_PLAN: Pipeline Correctness — Strike Recovery, Source Naming, Diagnostics

## Context

External review identified these issues:
1. **15m strike recovery**: If Verge restarts mid-window, first RTDS tick ≠ official opening reference
2. **Source naming**: `polymarket_ws_tick` is dead code, remaining labels ambiguous
3. **Diagnostics**: 24h averages misleading, need timestamp-aligned comparison
4. **RTDS ≠ TWAP**: RTDS is a point price; our 60s TWAP is a local reconstruction

## What's Already Correct

- RTDS connection: ✅ same `crypto_prices_chainlink` feed Polymarket uses
- Local TWAP labeled `reference_status = "estimated"`: ✅
- Ring buffer architecture: ✅
- Resolution audit table: ✅
- `polymarket_ws_tick`: deprecated dead code, no longer called from heartbeat

---

## Issue 1: 15m Strike Recovery

### Problem

Polymarket resolves 15m markets using "Chainlink Data Streams TWAP 60s" — a paid product. Verge cannot access this directly. Current fallback: first RTDS tick at window start from local DB. If Verge restarts mid-window, this tick may not exist or may be stale.

### The actual Polymarket resolution rule

> "resolve to 'Up' if the TWAP >= price at the beginning of that range"

The "price at the beginning" is the Chainlink BTC/USD reference at the exact market open time. Polymarket's own UI exposes this via a crypto-price endpoint (not officially documented, but used by current implementations).

### Plan: Multi-source strike recovery

For 15m markets where `priceToBeat` is absent from Gamma API:

```
Strike recovery cascade:
  1. Gamma API eventMetadata.priceToBeat (1h only, never for 15m)
  2. First RTDS tick at window start (from ring buffer, if Verge was running)
  3. First on-chain Chainlink tick at window start (from DB)
  4. Binance 1m candle open at window start
  5. Coinbase spot at window start
```

**New function** in `polymarket_fetcher.py`:

```python
def get_15m_opening_reference(window_start_ms: int) -> tuple[float | None, str]:
    """Recover the opening reference price for a 15m window.

    Tries multiple sources in order. Returns (price, source_label).
    """
    # 1. Ring buffer (RTDS ticks, fast)
    from polymarket_rtds import get_rtds_ticks
    ticks = get_rtds_ticks(since_ms=window_start_ms - 5_000)
    at_open = [t for t in ticks if t["timestamp_ms"] >= window_start_ms]
    if at_open:
        return at_open[0]["price"], "rtds_chainlink_tick"

    # 2. DB: on-chain Chainlink ticks
    # 3. Binance 1m candle open
    # 4. Coinbase spot at window_start
```

**Integration**: Call this in `_resolve_via_chainlink_ticks()` and in `_generate_signal_inner()` for 15m strike computation.

---

## Issue 2: Source Naming Cleanup

### Current → New

| Current | New | Why |
|---------|-----|-----|
| `polymarket_rtds` | `rtds_chainlink` | "Polymarket" is the platform, "Chainlink" is the oracle. clearer. |
| `polymarket_ws_tick` | (delete) | Dead code, deprecated, not called from heartbeat |
| `chainlink` | `chainlink_onchain` | Already labeled this way in diagnostics. Make consistent. |
| `polymarket_ws_twap_60s` | (already renamed) | Done in previous commit |

### Files to update

- `polymarket_rtds.py`: source label in `_handle_tick()` → `rtds_chainlink`
- `engine.py`: source references in `_resolve_via_chainlink_ticks()` and bar-building
- `db.py`: query filters using source labels
- `app.py`: diagnostics source labels
- `index.html`: source display labels
- `engine.py`: delete `record_polymarket_ws_tick()` and `_accumulate_ticks()` dead code

---

## Issue 3: Diagnostic Improvements

### Replace 24h averages with timestamp-aligned comparison

Current: `avg(price)` over 24h — meaningless for volatile assets.

New: Compare prices at the same timestamp.

```
Source Comparison (last 5 observations)
Timestamp           RTDS        Chainlink    Δ
12:31:01.120       63,675.20    63,674.91   +0.29
12:31:01.520       63,676.10    63,676.02   +0.08
...
```

### New diagnostics endpoint

Add `/api/source-comparison` that:
1. Gets last N RTDS ticks from ring buffer
2. Gets matching on-chain Chainlink ticks from DB (by timestamp proximity)
3. Computes per-timestamp delta
4. Returns: timestamp, rtds_price, chainlink_price, delta

### Dashboard panel

Replace "Source Breakdown (24h)" with "Feed Comparison" showing timestamp-aligned deltas.

---

## Issue 4: RTDS ≠ TWAP Clarification

### Current misleading naming

- Frontend label: "RTDS TWAP (est.)" — implies RTDS provides a TWAP
- Reality: RTDS is a point price; TWAP is our local reconstruction

### Fix

- Rename label: "RTDS Chainlink" (what it actually is)
- The TWAP is a local computation, not an RTDS feature
- Keep `reference_status = "estimated"` for the TWAP-derived price

---

## Files Modified

| File | Change |
|------|--------|
| `backend/polymarket_rtds.py` | Source label → `rtds_chainlink`, keep ring buffer |
| `backend/polymarket_fetcher.py` | Add `get_15m_opening_reference()` |
| `backend/engine.py` | Delete dead code, update source labels, use opening reference |
| `backend/db.py` | Update source label in queries |
| `backend/app.py` | Add source comparison endpoint, update diagnostics |
| `frontend/index.html` | Update labels, add feed comparison panel |

## Migrations

None needed. Source labels are strings in code, not DB schema.

## Validation

1. Run tests: `backend\venv\Scripts\python.exe -m pytest backend\tests\ -q`
2. Verify diagnostics show timestamp-aligned comparison
3. Verify 15m strike recovery works on restart mid-window
