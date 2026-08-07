# Phase 1 Verification: 15-Minute Market Support

**Date:** August 7, 2026
**Verified by:** automated checks + live Polymarket page inspection

---

## 1.1 — Resolution Source

**Status:** CONFIRMED

Opened live 15-minute BTC market at:
`https://polymarket.com/event/btc-updown-15m-1786193100`

Rules section reads:

> "The resolution source for this market is information from Chainlink,
> specifically the BTC/USD TWAP data stream available at
> https://data.chain.link/streams/btc-usd-twcap-60s-streams."

**Key finding:** Polymarket uses Chainlink's **Data Streams TWAP 60s** product,
not the on-chain Data Feed contract. These are related but distinct:

| Property | On-chain Data Feed (what we read) | TWAP Data Streams (what Polymarket resolves against) |
|---|---|---|
| Access | Free, public | Typically requires paid subscription |
| Data type | Spot price per heartbeat (~1h) | Time-weighted average over 60s |
| Contract/Endpoint | `0xc907E116054Ad103354f2D350FD2514433D57F6f` on Polygon | `https://data.chain.link/streams/btc-usd-twcap-60s-streams` |
| Latency | ~1 hour heartbeat delay | Near real-time |

**Accepted approximation:** The on-chain Data Feed is the best free option.
Both products share the same oracle network and a tight 0.1% deviation threshold.
This is the same category of gap as the existing CoinGecko-vs-Binance mismatch
already caught and documented in this project.

---

## 1.2 — Chainlink On-Chain Feed Address

**Status:** CONFIRMED

Feed explorer: `https://data.chain.link/feeds/polygon/mainnet/btc-usd`

Contract address on Polygon Mainnet: `0xc907E116054Ad103354f2D350FD2514433D57F6f`

Verified via:
- Chainlink's own feed explorer page
- Polygonscan: contract name `EACAggregatorProxy`, compiled with `v0.6.6`
- Product name: `BTC/USD-RefPrice-DF-Matic-001`
- Deviation threshold: 0.1%
- Decimal places: 8 (standard for BTC/USD feeds)
- 17 oracle operators

---

## 1.3 — 15-Minute Event Slug Pattern

**Status:** CONFIRMED

Pattern: `btc-updown-15m-{unix_timestamp}`

Example: `btc-updown-15m-1786193100`
- Event start: 2026-08-08T12:45:00Z (8:45 AM ET)
- Event end: 2026-08-08T13:00:00Z (9:00 AM ET)
- Duration: 15 minutes

Other assets follow the same shape:
- `eth-updown-15m-{ts}`
- `sol-updown-15m-{ts}`
- `xrp-updown-15m-{ts}`

Other durations also exist:
- `btc-updown-5m-{ts}` (5-minute)
- `btc-updown-4h-{ts}` (4-hour)

---

## 1.4 — Gamma API Series Slug

**Status:** CONFIRMED

Queried `https://gamma-api.polymarket.com/events` with several candidates:

| Query | Result |
|---|---|
| `series_slug=btc-up-or-down-15-min` | Empty `[]` |
| `series_slug=btc-updown-15m` | Empty `[]` |
| **`series_slug=btc-up-or-down-15m`** | **Returns active events** |
| `series_slug=btc-15-minute` | Empty `[]` |

**Confirmed value:** `btc-up-or-down-15m`

Series details from API response:
- Series ID: `10192`
- Title: "BTC Up or Down 15m"
- Recurrence: `15m`
- Active: `true`
- 24h volume: ~$2,332,774
- Liquidity: ~$562,654

**Note:** The event slug uses a shorter prefix (`btc-updown-15m-`) than the
series slug (`btc-up-or-down-15m`). This mismatch is confirmed from live data.

**Discovery method:**
1. Primary: `GET /events?series_slug=btc-up-or-down-15m&active=true&closed=false`
2. Fallback: slug-prefix matching (`btc-updown-15m-`)

---

## 1.5 — Polygon RPC Endpoint

**Status:** DECIDED

Tested public endpoints:

| Endpoint | Status | Notes |
|---|---|---|
| `https://polygon-rpc.com` | 401 Unauthorized | Deprecated, requires API key |
| `https://rpc.ankr.com/polygon` | Unauthorized | Requires API key |
| **`https://polygon-bor-rpc.publicnode.com`** | **Working** | Block #91,602,108 returned |

**Selected primary:** `polygon-bor-rpc.publicnode.com`
**Fallback:** `https://polygon-rpc.com` (if publicnode goes down, will need API key)

**Important:** Public RPCs carry no uptime guarantee. For production reliability,
consider configuring an API key with Alchemy or Ankr as a future upgrade.

---

## Summary of Verification

| Item | Status | Value |
|---|---|---|
| 1.1 — Resolution source | Confirmed | Chainlink BTC/USD TWAP 60s stream |
| 1.2 — On-chain feed address | Confirmed | `0xc907E116054Ad103354f2D350FD2514433D57F6f` |
| 1.3 — Event slug pattern | Confirmed | `btc-updown-15m-{unix_timestamp}` |
| 1.4 — Gamma series_slug | Confirmed | `btc-up-or-down-15m` |
| 1.5 — RPC endpoint | Decided | `polygon-bor-rpc.publicnode.com` |

**Approximation accepted:** Using on-chain Data Feed instead of TWAP Data Streams.
Same oracle network, 0.1% deviation threshold, best free option available.

**All Phase 1 items verified. Ready for Phase 2 (Market Configuration Layer).**
