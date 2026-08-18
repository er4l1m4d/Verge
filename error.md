# Error Log

## Error: Resolution stopped after 1000 observations
Date: 2026-08-11
Step: Resolution / heartbeat
File(s): backend/db.py (`get_unresolved_window_outcomes`)

### Cause
Supabase has a project-level API setting that hard-caps query results at 1000 rows. The `get_unresolved_window_outcomes` function queried `window_observations` without an explicit row range. Once the observations table exceeded 1000 rows, newer windows were invisible to the resolver — it only saw the oldest 1000 rows (which already had outcomes), so it returned 0 unresolved.

This affected BOTH 1h and 15m resolution simultaneously.

### Fix
Replaced unbounded `.execute()` queries with `.range(offset, offset + page_size - 1)` pagination in `get_unresolved_window_outcomes`. Each page fetches 1000 rows; the function iterates until all rows are consumed. This bypasses the hard cap.

### Prevention
- Any query that needs to see all rows must use `.range()` pagination
- `.limit()` does NOT override the project-level cap
- Watch for this pattern: if resolution "stops working" after the table grows, it's likely this issue

## Error: Three different strike values for same 15m market
Date: 2026-08-18
Step: Price reference / diagnostics
File(s): backend/app.py, backend/polymarket_fetcher.py, backend/engine.py

### Cause
Three separate functions computed different strike values for the same 15m market:
- Candles endpoint: Chainlink bar `open` at window start
- Diagnostics: on-chain Chainlink tick at window start
- Signal generation: `get_15m_opening_reference()` 60s TWAP

Each source gave a different value ($64,280 vs $64,430 vs another), making diagnostics unreliable and the main page strike inconsistent with what signals used.

### Fix
Unified all three paths to use `get_15m_opening_reference()` (60s TWAP at window start):
- Candles endpoint now calls `get_15m_opening_reference()` instead of bar open
- `get_polymarket_live_market()` 15m fallback now calls `get_15m_opening_reference()` instead of raw tick
- Added `strike_source`, `quality_status`, `reference_age_ms` columns to signals table (migration 014)
- Added Reference Audit and Strike Source Distribution panels to diagnostics frontend

### Prevention
- Any new strike computation must go through `get_15m_opening_reference()`, not a separate path
- `strike_source` on every signal provides provenance trail
- `quality_status` trust tiers prevent proxy fallbacks from being labeled as trusted
