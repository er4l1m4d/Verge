# BUILD_PLAN: RTDS Hardening — Close Gaps vs FrondEnt Architecture

## Audit Summary

### What FrondEnt does (3-layer price architecture)

| Layer | File | Protocol | Latency | Purpose |
|-------|------|----------|---------|---------|
| 1 | `polymarketLiveWs.js` | Polymarket RTDS WS | ~100ms | Primary: same feed Polymarket resolves with |
| 2 | `chainlinkWs.js` | Polygon WSS → `AnswerUpdated` events | ~2s | Fallback: real-time on-chain Chainlink |
| 3 | `chainlink.js` | Polygon HTTP RPC → `latestRoundData` | ~5s | Emergency fallback: on-chain poll |

Key patterns:
- All layers return `{ getLast(), close() }` interface
- `getLast()` reads from memory (no DB hit)
- Auto-reconnect: 500ms initial, 1.5x multiplier, 10s cap
- Multiple RPC candidates with health tracking (preferred RPC)
- Proper `eth_unsubscribe` on close
- 2s cache on HTTP fallback, 1.5s RPC timeout

### What Verge has now

| Layer | File | Protocol | Latency | Purpose |
|-------|------|----------|---------|---------|
| 1 | `polymarket_rtds.py` | Polymarket RTDS WS | ~100ms | Primary: persistent Chainlink stream |
| 2 | `chainlink_fetcher.py` | Polygon HTTP RPC only | ~5s | On-chain fallback (HTTP only, no WSS) |
| 3 | `data_fetcher.py` | Binance/Coinbase/CoinGecko | ~1-3s | Spot price fallback |

### Gaps identified

| # | Gap | FrondEnt has | Verge has | Risk |
|---|-----|-------------|-----------|------|
| 1 | **On-chain Chainlink WSS** | `chainlinkWs.js` — subscribes to `AnswerUpdated(int256,uint256,uint256)` events via Polygon WSS | None — HTTP-only polling | When RTDS is down, Verge falls back to HTTP (5s latency) instead of real-time WSS (2s) |
| 2 | **Multiple RPC candidates** | 5+ RPC URLs with health tracking | Single `POLYGON_RPC_URL` | Single point of failure for on-chain reads |
| 3 | **RPC health/preference** | Tracks which RPC responded fastest, prefers it next call | No health tracking | Slow/dead RPC degrades fallback chain |
| 4 | **Reconnection tuning** | 500ms → 10s (1.5x) | 2s → 15s (2x) | Slower recovery from transient disconnects |
| 5 | **In-memory price cache** | `getLast()` returns price from memory, no DB hit | Every tick written to DB, TWAP reads from DB | DB read latency on every signal generation |
| 6 | **Unsubscribe on close** | Sends `eth_unsubscribe` before closing WSS | No WSS for on-chain, RTDS has no explicit unsubscribe | Minor: Polymarket WS doesn't require it |
| 7 | **RPC timeout** | 1.5s timeout per RPC call | 30s timeout (requests default) | Slow RPC blocks entire fallback chain |

## Hardening Plan

### Phase 1: On-chain Chainlink WSS fallback

**File:** `backend/chainlink_ws.py` (NEW)

Add a persistent WebSocket subscription to Chainlink's `AnswerUpdated` events on Polygon. This provides real-time on-chain price updates when RTDS is unavailable, matching FrondEnt's `chainlinkWs.js`.

**Key decisions:**
- Use `websockets` library (already in venv) — NOT `ethers` (Node.js only)
- Subscribe to logs filtered by aggregator address and `AnswerUpdated` topic
- Parse `int256 answer` from log topics[1], `uint256 updatedAt` from log data
- Return `{ get_last(), close() }` interface (Python dict, not JS object)
- Exponential backoff: 500ms → 10s (1.5x multiplier) — matching FrondEnt

**Contract details:**
- Address: `0xc907E116054Ad103354f2D350FD2514433D57F6f` (Polygon Mainnet)
- Topic0: `keccak256("AnswerUpdated(int256,uint256,uint256)")`
- Decimals: 8 (BTC/USD)
- answer = `int256(topics[1]) / 10^8`
- updatedAt = `uint256(data) * 1000` (seconds → ms)

### Phase 2: Multiple RPC candidates + health tracking

**File:** `backend/chainlink_fetcher.py` (MODIFY)

Add multiple RPC URL support with health tracking:
- Default candidates: `polygon-rpc.com`, `rpc.ankr.com/polygon`, `polygon.llamarpc.com`
- Track response times per RPC
- Prefer fastest RPC for next call
- 1.5s timeout per RPC call (down from 30s)

### Phase 3: Wire WSS fallback into resolution chain

**File:** `backend/engine.py` (MODIFY)

Update the price source chain to include WSS:
```
RTDS → Chainlink WSS → Chainlink HTTP → Binance
```

- `get_current_price_data_for_duration()`: Try RTDS ticks first, then WSS cache, then HTTP
- `_resolve_via_chainlink_ticks()`: Try RTDS first, then WSS cache, then HTTP
- TWAP computation: Use in-memory cache when available

### Phase 4: In-memory price cache

**File:** `backend/chainlink_ws.py` (NEW)

Add `get_chainlink_ws_price()` function that returns the latest price from the WSS subscription memory cache. This avoids DB reads for real-time price checks.

**Integration points:**
- `get_current_price_data_for_duration()`: Check WSS cache before hitting DB
- Diagnostics: Show WSS price alongside RTDS price
- Signal generation: Use WSS cache for `current_price` when RTDS is stale

### Phase 5: Reconnection tuning

**Files:** `backend/polymarket_rtds.py`, `backend/chainlink_ws.py` (MODIFY)

Standardize reconnection parameters:
- Initial delay: 500ms (down from 2s)
- Multiplier: 1.5x (down from 2x)
- Max delay: 10s (down from 15s)

Matches FrondEnt's proven reconnection strategy.

### Phase 6: Diagnostics update

**File:** `backend/app.py` (MODIFY)

Add WSS health to diagnostics:
- `chainlink_ws_price`: Latest price from WSS subscription
- `chainlink_ws_age_ms`: Age of last WSS update
- `chainlink_ws_running`: Whether WSS thread is alive
- `rpc_health`: Response times per RPC candidate

**File:** `frontend/index.html` (MODIFY)

Add WSS price source to Live Prices section.

## Files Modified

| File | Change |
|------|--------|
| `backend/chainlink_ws.py` | NEW — persistent on-chain Chainlink WSS subscription |
| `backend/chainlink_fetcher.py` | Multiple RPC candidates, health tracking, 1.5s timeout |
| `backend/polymarket_rtds.py` | Reconnection tuning (500ms → 10s) |
| `backend/engine.py` | Wire WSS fallback into price chain, in-memory cache |
| `backend/app.py` | Add WSS health to diagnostics |
| `frontend/index.html` | Add WSS price source label |

## Files NOT modified

| File | Reason |
|------|--------|
| `backend/data_fetcher.py` | Binance/Coinbase remain as-is |
| `backend/db.py` | No schema changes |
| `backend/polymarket_fetcher.py` | Resolution logic already works |

## Validation

1. **WSS connection:** Verify `chainlink_ws.py` connects to Polygon WSS and receives `AnswerUpdated` events
2. **Price accuracy:** Compare WSS price against RTDS and HTTP — should be within $1
3. **Fallback chain:** Kill RTDS thread → verify WSS provides price → kill WSS → verify HTTP provides price
4. **Reconnection:** Kill WSS connection → verify reconnects within 10s
5. **RPC health:** Verify fastest RPC is preferred on subsequent calls
6. **Diagnostics:** Check WSS health badge appears in diagnostics page
7. **Run tests:** `backend\venv\Scripts\python.exe -m pytest backend\tests\ -q`
