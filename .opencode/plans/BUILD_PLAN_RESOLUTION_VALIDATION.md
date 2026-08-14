# Build Plan: Resolution Validation + 15m Price Mirroring

Addresses two related problems:
1. Verge resolves markets locally via Binance — no validation against Polymarket's official resolution
2. 15m strike price and current price don't match what Polymarket shows

## Phase 1 — Extract conditionId + Add Resolution Query

**Goal:** Make it possible to query Polymarket's official resolution for any closed market.

**1.1 — Extract `conditionId` in `get_current_market()` (engine.py)**
How: Add `conditionId` (raw Gamma field) to the market dict returned by `get_current_market()`. Also extract `outcomes` (the label array like `["Up","Down"]`).
Done when: every market dict includes `condition_id` and `outcome_labels`.

**1.2 — Store `condition_id` on the signal (engine.py)**
How: Add `condition_id: str | None = None` to `LiveSignal` dataclass. Set it from the market dict in `_generate_signal_inner()`. Pass through `persist_signal()` to the `signals` table.
Done when: every signal row has a `condition_id`.

**1.3 — Add `get_polymarket_resolution(condition_id)` (polymarket_fetcher.py)**
How:
```python
def get_polymarket_resolution(condition_id: str) -> dict | None:
    """Query Gamma API for a closed market's official resolution.

    Returns {"outcome": "UP"|"DOWN", "closed_time": str, "raw": dict} or None.
    """
    resp = requests.get(f"{GAMMA_BASE}/markets/{condition_id}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("closed"):
        return None
    prices = data.get("outcomePrices")  # e.g. '["1.00","0.00"]'
    outcomes = data.get("outcomes")     # e.g. '["Up","Down"]'
    if isinstance(prices, str):
        prices = json.loads(prices)
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if not prices or not outcomes:
        return None
    # Winner is the outcome whose price settled to ~1.0
    winner_idx = 0 if float(prices[0]) > 0.9 else 1
    outcome = outcomes[winner_idx].upper()  # "UP" or "DOWN"
    return {"outcome": outcome, "closed_time": data.get("closedTime"), "raw": data}
```
Done when: function returns UP/DOWN for a closed market, None for active.

**1.4 — Add `condition_id` to `signals` table (migration 010)**
How:
```sql
ALTER TABLE signals ADD COLUMN IF NOT EXISTS condition_id TEXT;
```
Done when: migration runs cleanly.

---

## Phase 2 — Wire Validation into Resolution

**Goal:** After Verge resolves a window, check Polymarket's official outcome and log agreement.

**2.1 — Add `official_outcome` to `window_outcomes` (migration 010)**
How:
```sql
ALTER TABLE window_outcomes ADD COLUMN IF NOT EXISTS official_outcome TEXT;
ALTER TABLE window_outcomes ADD COLUMN IF NOT EXISTS resolution_agreement BOOLEAN;
```
Done when: migration runs cleanly.

**2.2 — Update `write_window_outcome()` to accept official_outcome (db.py)**
How: add optional `official_outcome` parameter. If provided, also set `resolution_agreement = (outcome == official_outcome)`.
Done when: function accepts the new param.

**2.3 — Query Polymarket resolution after local resolution (engine.py)**
How: In `resolve_previous_hour()`, after writing the local outcome to `window_outcomes`:
1. Look up the signal for this window to get `condition_id`
2. If `condition_id` exists, call `get_polymarket_resolution(condition_id)`
3. If Polymarket has resolved, compare and store `official_outcome` + `resolution_agreement`
4. Log mismatches at WARNING level
Done when: every resolved window with a condition_id gets validated.

---

## Phase 3 — Diagnostics: Resolution Agreement

**Goal:** See how often Verge's resolution matches Polymarket's.

**3.1 — Add resolution agreement query (db.py)**
How:
```python
def get_resolution_agreement(client) -> dict:
    """Compare local outcomes vs official Polymarket outcomes."""
    resp = client.table("window_outcomes") \
        .select("market_duration,actual_outcome,official_outcome,resolution_agreement") \
        .not_.is_("official_outcome", "null") \
        .execute()
    rows = resp.data or []
    # Group by duration, count agreements/disagreements
    ...
```
Done when: function returns per-duration agreement stats.

**3.2 — Add to diagnostics endpoint (app.py)**
How: Add `resolution_agreement` section to the `/api/diagnostics` response.
Done when: endpoint returns agreement data.

**3.3 — Add Resolution Agreement panel to diagnostics page (index.html)**
How: Table showing `Duration | Total | Agreements | Disagreements | Agreement %`.
Done when: panel renders with real data.

---

## Phase 4 — Investigate 15m Price Mirroring

**Goal:** Understand why 15m strike and current prices diverge from Polymarket.

**4.1 — Add Polymarket live prices to diagnostics**
How: In `get_polymarket_resolution()` or a new function, also query the live market's `outcomePrices` (for active markets) and `priceToBeat`. Compare against what Verge reports.
Done when: diagnostics shows both Verge's prices and Polymarket's prices side by side.

**4.2 — Add price comparison to diagnostics page**
How: New section "Polymarket vs Verge Prices" showing:
- Strike: Verge's strike vs Polymarket's `priceToBeat`
- Current: Verge's current price vs Polymarket's live `outcomePrices` midpoint
Done when: comparison renders.

**4.3 — Analyze and fix root cause**
How: Based on the comparison data, identify whether the issue is:
- Strike extraction (recursive key search / text parsing missing the right field)
- Current price (TWAP not aligned with Polymarket's Chainlink feed)
- Timing (prices fetched at different moments)
Done when: root cause identified and fix applied.

---

## Files Changed

| File | Changes |
|------|---------|
| `engine.py` | Extract condition_id, store on LiveSignal, query Polymarket after resolution |
| `polymarket_fetcher.py` | Add `get_polymarket_resolution()` |
| `db.py` | Add `condition_id` to SignalRow, `official_outcome`/`resolution_agreement` to WindowOutcomeRow, agreement query |
| `app.py` | Add agreement data to diagnostics endpoint, add price comparison |
| `migrations/010_resolution_validation.sql` | ALTER TABLE for condition_id, official_outcome, resolution_agreement |
| `index.html` | Add Resolution Agreement panel + Polymarket vs Verge Prices panel |
