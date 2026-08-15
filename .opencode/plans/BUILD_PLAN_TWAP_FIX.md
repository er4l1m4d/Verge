# Build Plan: Fix compute_twap() PriceSnapshotRow Access

## Problem

`get_recent_price_snapshots()` returns `PriceSnapshotRow` dataclass objects (with `.timestamp_ms`, `.price` attributes), but `compute_twap()` uses dict subscript access (`t["timestamp_ms"]`, `t["price"]`). This crashes every time TWAP is called, silently swallowed by the outer exception handler — causing 15m signals to fall back to SKIP decisions that aren't real market conditions.

## Fix

**File:** `engine.py` — `compute_twap()` (lines 1022-1045)

Switch all dict subscript access to attribute access:
- `t["timestamp_ms"]` → `t.timestamp_ms`
- `t["price"]` → `t.price`
- `tick["timestamp_ms"]` → `tick.timestamp_ms`
- `tick["price"]` → `tick.price`
- `ticks[-1]["price"]` → `ticks[-1].price`

Update type hint and docstring to reflect `list[PriceSnapshotRow]`.

No logic changes needed — the sorting, windowing, and weighting math all stay the same.

## Validation

1. Run tests (53 tests)
2. Syntax check `engine.py`
3. Push to master
