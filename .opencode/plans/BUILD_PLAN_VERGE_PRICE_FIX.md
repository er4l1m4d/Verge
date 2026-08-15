# Build Plan: Fix Verge Price in Diagnostics Panel

## Problem

The "Polymarket Live Market Prices" panel has always shown empty Verge Price and Difference columns. The frontend already supports them (line 3703 passes `null`), but the backend never sends the data.

## Root Cause

1. **Backend** (`app.py:846-852`): Builds `polymarket_live` with raw `get_polymarket_live_market()` results — no `verge_price` field
2. **Frontend** (`index.html:3703`): Hardcodes `null` for the `vergePrice` parameter: `pmRow('15m', pmLive['15m'], null)`

## Fix

### Backend (`app.py` — `/api/diagnostics`)

After computing the `live` dict (line 820-825), compute Verge's best available price:

```python
verge_price = (
    live.get("polymarket_ws_twap_60s")
    or live.get("chainlink_onchain")
    or live.get("pyth")
    or live.get("coinbase_spot")
)
```

Then enrich each Polymarket market with `verge_price` and `difference`:

```python
def _enrich_pm(pm_data):
    if not pm_data or not verge_price:
        return pm_data
    strike = pm_data.get("price_to_beat")
    return {
        **pm_data,
        "verge_price": verge_price,
        "difference": round(abs(strike - verge_price), 2) if strike else None,
    }

polymarket_live = {
    "15m": _enrich_pm(pm_15m),
    "1h": _enrich_pm(pm_1h),
}
```

### Frontend (`index.html` — diagnostics render)

Change line 3703 from hardcoded `null` to the actual `verge_price` from the response:

```js
const pmRows = pmRow('15m', pmLive['15m'], pmLive['15m']?.verge_price)
             + pmRow('1h', pmLive['1h'], pmLive['1h']?.verge_price);
```

## Files Changed

| File | Changes |
|------|---------|
| `app.py` | Compute `verge_price` from live sources, add `verge_price` + `difference` to each Polymarket market |
| `index.html` | Pass `pmLive[duration]?.verge_price` instead of `null` |

## Validation

1. Run tests
2. Check diagnostics page — "Verge Price" and "Difference" columns should populate
