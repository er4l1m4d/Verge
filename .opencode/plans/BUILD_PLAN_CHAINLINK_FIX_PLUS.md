# Build Plan: Chainlink Tick Resolution Fix + Broader Import Deadlock

Two fixes based on new Render log evidence, plus a minor correction to the previous Polymarket resolution fix.

---

## Priority 1 — Missing `import db` in `_resolve_via_chainlink_ticks()`

**File:** `engine.py` — `_resolve_via_chainlink_ticks()` (line 936)

**Root cause:** Function uses `db.get_price_snapshots(...)` at line 944 but has no local `import db`. Every other function that calls `db` directly has its own local import (e.g., `resolve_previous_hour()` at line 805), but this one was missed.

**Effect:** Chainlink tick resolution always fails with `NameError: name 'db' is not defined`, falling through to Coinbase/Binance fallbacks. The Chainlink ticks that ARE being recorded (now that the accumulator is fixed) can never be used for resolution.

**Fix:** Add `import db` at the top of the function, before the try block.

---

## Priority 2 — Broader import deadlock: add `import netrc`

**File:** `app.py` — module-level imports (line 20)

**Root cause:** The previous `import db` fix covered `websockets`, but `netrc` is another module lazily imported by `requests` (deep inside HTTP calls) that can race with the accumulator thread at startup. The import-lock deadlock pattern is general — any module first imported on the main thread can race against background threads.

**Fix:** Add `import netrc` alongside `import db` at module load. Note: this is best-effort — a third module could still cause the same pattern. The general solution would be to pre-import all commonly lazy-loaded modules, or restructure the accumulator to start later.

---

## Minor correction — Polymarket resolution param name

**File:** `polymarket_fetcher.py` — `get_polymarket_resolution()` (line 155)

**Current:** `params={"condition_id": condition_id}` (singular)
**Correct:** `params={"condition_ids": condition_id}` (plural)

The Gamma API filtering endpoint uses `condition_ids` as the parameter name (per `polymarket-apis` package documentation). The singular form may work but the plural is the documented convention.

**Note:** The user's analysis shows the path-style `/markets/{condition_id}` actually worked for a closed market (returned "not closed yet" correctly, not a 422). The 422 was likely specific to that one condition ID, not a systemic issue. The query-param approach is still better for robustness, but the param name should be `condition_ids` (plural).

---

## Files Changed

| File | Changes |
|------|---------|
| `engine.py` | Add `import db` at top of `_resolve_via_chainlink_ticks()` |
| `app.py` | Add `import netrc` at module load alongside `import db` |
| `polymarket_fetcher.py` | Fix param name: `condition_id` → `condition_ids` |

## Validation

1. Run tests (53 tests)
2. Syntax check all 3 files
3. Push to master
