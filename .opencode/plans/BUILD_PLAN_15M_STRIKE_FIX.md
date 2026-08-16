# BUILD_PLAN: 15m Strike Price Fix

## Root Cause

**Confirmed via Gamma API**: Polymarket 15m events have **no `eventMetadata` key at all**. The `eventMetadata.priceToBeat` field only exists on 1h events.

API evidence:
- 1h: `"eventMetadata": {"finalPrice": 77517.45, "priceToBeat": 77485.14}` ✅
- 15m: no `eventMetadata` key in event object ❌

This means the entire extraction chain (price_to_beat → recursive key search → text parsing) **always fails for 15m** — it's expected, not a bug. The fallback at `engine.py:594-608` is the correct primary path for 15m.

**The problem**: There is no log confirming whether the Chainlink bar or Coinbase fallback actually produces a value. After `"All extraction methods failed, using candle/Chainlink fallback"`, the code silently falls through. If both fallbacks fail, `strike_price` stays `None` with no warning.

## Phases

### Phase 1: Skip extraction chain for 15m (engine.py:580-593)

**Why**: 15m markets never have `priceToBeat`. The recursive key search and text parsing always waste cycles and always fail. Skip them.

**File**: `backend/engine.py` ~lines 580-593

**Change**: After `strike_price = market.get("price_to_beat")`, if that's `None` and `duration == "15m"`, skip the recursive search and text parsing — go straight to the fallback block.

```python
# Current:
strike_price = market.get("price_to_beat")
if strike_price is not None:
    log.info(...)
else:
    strike_price = extract_strike_from_market(market)  # always fails for 15m
    if strike_price is not None:
        log.info(...)
    else:
        strike_price = parse_strike_from_text(market)  # always fails for 15m
        if strike_price is not None:
            log.info(...)

# Fixed:
strike_price = market.get("price_to_beat")
if strike_price is not None:
    log.info(f"[{duration}] Using official Polymarket strike: ${strike_price:,.2f}")
elif duration == "15m":
    # 15m markets never have eventMetadata.priceToBeat — skip extraction chain
    log.debug(f"[15m] No official Polymarket strike (expected), computing from Chainlink bars")
else:
    strike_price = extract_strike_from_market(market)
    if strike_price is not None:
        log.info(...)
    else:
        strike_price = parse_strike_from_text(market)
        if strike_price is not None:
            log.info(...)
```

### Phase 2: Add diagnostic logging in the fallback (engine.py:594-608)

**Why**: We need to know whether the fallback succeeds or fails silently.

**File**: `backend/engine.py` ~lines 594-608

**Changes**:
1. Before the Chainlink bar filter: log `df_price` row count, `hour_open_time` value, and the min/max `open_time` in `df_price` so we can see time alignment.
2. After the filter: log how many bars matched.
3. After the Coinbase fallback: add a WARNING if `strike_price` is still `None`.

```python
if strike_price is None:
    log.info(f"[{duration}] Computing strike from price data (fallback)")
    if duration == "15m" and hour_open_time is not None:
        if len(df_price) > 0:
            log.info(
                f"[15m] df_price: {len(df_price)} bars, "
                f"open_time range: {df_price['open_time'].min()}-{df_price['open_time'].max()}, "
                f"target: {hour_open_time}"
            )
            matching = df_price[df_price["open_time"] >= hour_open_time]
            if len(matching) > 0:
                strike_price = float(matching.iloc[0]["open"])
                log.info(f"[15m] Strike from Chainlink bars: ${strike_price:,.2f}")
            else:
                log.warning(
                    f"[15m] No Chainlink bar with open_time >= {hour_open_time} "
                    f"(bars end at {df_price['open_time'].max()})"
                )
        # Fallback: Coinbase if Chainlink bars unavailable
        if strike_price is None:
            from data_fetcher import get_price_at_time
            strike_price = get_price_at_time(hour_open_time)
            if strike_price:
                log.info(f"[15m] Strike from Coinbase fallback: ${strike_price:,.2f}")
            else:
                log.warning(f"[15m] Coinbase fallback also failed for hour_open_time={hour_open_time}")
```

### Phase 3: Log final strike_price status (engine.py ~line 618)

**Why**: After all extraction attempts, confirm whether we have a strike or not.

**File**: `backend/engine.py` before line 619 (`return LiveSignal(...)`)

**Change**: Add a warning if strike_price is still None after everything.

```python
if strike_price is None:
    log.warning(f"[{duration}] No strike price available from any source")
```

## Files Modified

| File | Change |
|------|--------|
| `backend/engine.py` | Skip extraction for 15m, diagnostic logging in fallback, final strike warning |

## Validation

1. Deploy to Render and observe logs for 15m signals
2. Confirm: no more "All extraction methods failed" for 15m (replaced by debug log)
3. Confirm: Chainlink bar fallback logs show bar count and time range
4. Confirm: strike_price is populated for 15m signals (not None)
5. If fallback fails: the warning logs will show exactly why (time mismatch, empty df, Coinbase failure)
