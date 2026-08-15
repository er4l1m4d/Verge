# Build Plan: Fix 4 Live Bugs from Render Logs

Priority-ordered fixes based on actual Render log output.

---

## Priority 1 — WebSocket accumulator tight reconnect loop

**File:** `engine.py` — `_accumulate_ticks()` (line 1071) + `_run()` (line 1057)

**Root cause:** `json.loads(raw)` at line 1086 crashes on the first non-JSON frame (empty string or protocol handshake). The outer `_run()` loop catches it, sleeps flat 5s, retries — creating an infinite rapid reconnect cycle.

**Fix:**
1. In `_accumulate_ticks()`: wrap `json.loads(raw)` in try/except to skip non-JSON messages instead of crashing
2. In `_run()`: add exponential backoff (5s → 10s → 20s → 60s cap) with a reset on successful connect

---

## Priority 2 — Chainlink `Web3` NameError

**File:** `chainlink_fetcher.py` — `_get_contract()` (line 94)

**Root cause:** `_get_contract()` line 100 calls `Web3.to_checksum_address(FEED_ADDRESS)` but `Web3` is only imported inside `_get_web3()` (line 89), not in `_get_contract()`'s scope. Every call to `get_chainlink_price()` hits this NameError.

**Fix:** Add `from web3 import Web3` inside `_get_contract()` before the `Web3.to_checksum_address()` call.

---

## Priority 3 — "No current event loop in thread MainThread"

**File:** `engine.py` — `record_polymarket_ws_tick()` (line 774)

**Root cause:** `asyncio.get_event_loop().run_until_complete(...)` fails in Python 3.12+ when no event loop exists in the thread. This is a redundant path — the accumulator thread (Priority 1 fix) handles tick recording.

**Fix:** Replace `asyncio.get_event_loop().run_until_complete(...)` with `asyncio.run(...)`. This creates a fresh event loop per call, works correctly from synchronous context.

---

## Priority 4 — Per-heartbeat full table scan of window_observations

**File:** `db.py` — `get_unresolved_window_outcomes()` (line 395)

**Root cause:** Paginates through the ENTIRE `window_observations` table (7+ pages of 1000 rows) every heartbeat to find unresolved windows. Table only grows.

**Fix:** Add a `since_ms` parameter (default: 48 hours ago) and filter `gte("market_window_start", since_ms)` on both the observations and outcomes queries. No window older than 48h needs resolution.

---

## Files Changed

| File | Changes |
|------|---------|
| `engine.py` | Exponential backoff in `_run()`, JSON parse guard in `_accumulate_ticks()`, `asyncio.run()` in `record_polymarket_ws_tick()` |
| `chainlink_fetcher.py` | Add `from web3 import Web3` inside `_get_contract()` |
| `db.py` | Add `since_ms` filter to `get_unresolved_window_outcomes()` |

## Validation

1. Run tests (53 tests, all should pass)
2. Syntax check all 3 files
3. Push to master
