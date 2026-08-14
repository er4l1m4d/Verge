# Build Plan: Polymarket 15-Minute BTC Price Reference & TWAP Mirror Layer

**Status:** PLANNING  
**Target:** BTC 15-minute Polymarket markets  
**Purpose:** Add a robust, free-to-operate price-reference layer to Verge that can closely track the BTC price stream relevant to Polymarket's 15-minute markets, reconstruct a local 60-second TWAP estimate, preserve raw evidence, measure discrepancies between price sources, and integrate safely into Verge without changing the existing 1-hour behavior.

> **Important:** This plan deliberately does **not** claim that a locally reconstructed TWAP is the official Chainlink Data Streams settlement value. The official resolution source remains the Chainlink BTC/USD TWAP Data Stream identified in the market rules. The public Polymarket RTDS `crypto_prices_chainlink` feed is treated as a high-value free reference source, and the local TWAP is treated as a reconstruction/estimate until empirical validation proves how closely it tracks the official result.

---

# 0. Executive Decision

The implementation should **not** be:

```text
Binance price
    ↓
calculate TWAP
    ↓
pretend it is Polymarket's official price
```

It should be:

```text
                    ┌──────────────────────────────┐
                    │ Polymarket RTDS              │
                    │ crypto_prices_chainlink      │
                    │ btc/usd                      │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                         Always-On Collector
                                   │
             ┌─────────────────────┼─────────────────────┐
             │                     │                     │
             ▼                     ▼                     ▼
      Raw Chainlink RTDS    Local 60s TWAP       On-chain Chainlink
          observations         reconstruction          reference
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   │
                                   ▼
                         Market Window Engine
                                   │
                   ┌───────────────┼───────────────┐
                   ▼               ▼               ▼
                P2B state      Live reference    Final state
                   │               │               │
                   └───────────────┼───────────────┘
                                   ▼
                           Verge Signal Engine
                                   │
                                   ▼
                         Supabase / Dashboard

Additional comparison-only sources:

Binance BTC/USDT ───────────────┐
Coinbase BTC/USD ───────────────┤
                                 ▼
                         Reference divergence
```

## Core design rule

The system must maintain a distinction between:

1. **Official resolution source**  
   Chainlink BTC/USD TWAP Data Stream used by the market's resolution rules.

2. **Polymarket RTDS Chainlink reference**  
   Public `crypto_prices_chainlink` observations exposed by Polymarket.

3. **Local reconstructed 60-second TWAP**  
   Verge's own calculation over the RTDS observation stream.

4. **On-chain Chainlink Data Feed reference**  
   Existing free approximation already used by Verge.

5. **Exchange references**  
   Binance/Coinbase prices used for context, forecasting, and divergence analysis — never silently substituted for the resolution source.

---

# 1. Scope and Non-Goals

## In Scope

This build adds:

- an always-on Polymarket RTDS collector;
- raw `btc/usd` Chainlink-source observation storage;
- source-time and receive-time tracking;
- reconnect and gap detection;
- local 60-second TWAP reconstruction;
- market-window association;
- price-to-beat/reference capture;
- live current reference;
- final reference snapshot;
- source-to-source divergence metrics;
- historical validation against eventual Polymarket outcomes;
- Supabase persistence;
- a Verge API/state layer;
- tests and failure simulations;
- confidence/quality flags;
- a paper-trading-only integration into the existing 15-minute engine.

## Explicit Non-Goals

Do **not** build:

- a fake "official Chainlink resolver";
- a system that declares local TWAP to be exact without evidence;
- a Binance-only settlement simulator;
- a CSV-only historical store;
- a connection that starts only shortly before market open;
- a 1-hour architecture rewrite;
- real-money trading;
- hidden fallbacks that silently change the resolution source.

---

# 2. Current Verge Context

The existing repository already has:

- duration-specific market configuration;
- a 15-minute market path;
- an on-chain Chainlink price reader;
- Binance/Coinbase/CoinGecko price infrastructure;
- Supabase persistence;
- continuous window observations;
- universal window outcome storage;
- backtesting and outcome comparison.

The existing 15-minute plan intentionally accepts the on-chain Chainlink Data Feed as a **free approximation** to the Chainlink Data Streams TWAP resolution source.

This new plan should therefore be treated as a **new price-reference/data-quality layer**, not a replacement for the entire 15-minute engine.

## Existing modules to reuse

Likely reuse points:

```text
backend/
├── engine.py
├── market_config.py
├── chainlink_fetcher.py
├── data_fetcher.py
├── polymarket_fetcher.py
├── db.py
├── data_alignment.py
├── backtest.py
├── report.py
└── tests/
```

Likely new modules:

```text
backend/
├── polymarket_rtds.py
├── price_reference.py
├── twap.py
├── market_window.py
├── price_quality.py
└── tests/
    ├── test_rtds.py
    ├── test_twap.py
    ├── test_market_window.py
    └── test_price_quality.py
```

Names are suggestions. Fit them to the existing repository conventions rather than creating duplicate abstractions.

---

# 3. Phase 1 — Freeze the Existing 1-Hour Path

**Goal:** Ensure this work cannot accidentally change existing hourly behavior.

## 1.1 Establish baseline tests

Run the complete current test suite before modifying anything.

Record:

```text
test count
pass count
fail count
coverage if currently available
1h signal example
1h API response example
```

Save a baseline report under:

```text
reports/
```

## 1.2 Snapshot existing 1h behavior

Capture:

- current 1h signal;
- current odds;
- current price;
- current indicators;
- current fee calculation;
- current API response shape.

## 1.3 Add an isolation rule

Every new 15m price-reference feature must enter the system through duration-specific configuration.

Do not insert 15m-only logic into generic functions without a duration guard.

## Avoid

- changing `data_fetcher.py` behavior globally;
- replacing Binance for the existing 1h path;
- renaming existing response keys without compatibility;
- changing 1h scoring constants;
- changing the current graduation gate.

## Done When

- existing tests remain green;
- a known 1h signal is unchanged;
- all new code can be disabled without breaking the 1h path.

---

# 4. Phase 2 — Verify the Real Polymarket RTDS Protocol

**Goal:** Implement the actual documented RTDS protocol before writing the collector.

Polymarket currently documents:

```text
WebSocket:
wss://ws-live-data.polymarket.com

Chainlink topic:
crypto_prices_chainlink

symbol:
btc/usd
```

The documented subscription model uses:

```json
{
  "action": "subscribe",
  "subscriptions": [
    {
      "topic": "crypto_prices_chainlink",
      "type": "*",
      "filters": "{\"symbol\":\"btc/usd\"}"
    }
  ]
}
```

The documented message shape is:

```json
{
  "topic": "crypto_prices_chainlink",
  "type": "update",
  "timestamp": 1753314088421,
  "payload": {
    "symbol": "btc/usd",
    "timestamp": 1753314088395,
    "value": 67234.50
  }
}
```

## 2.1 Create a protocol fixture

Store real observed RTDS messages under:

```text
backend/tests/fixtures/rtds/
```

At minimum capture:

```text
normal_update.json
multiple_updates.json
unexpected_message.json
disconnect_fixture.json
```

Do not invent fixture payloads when you can capture real ones.

## 2.2 Record both timestamps

Every observation must retain:

```text
event_timestamp
received_timestamp
```

Where:

- `event_timestamp` = timestamp supplied by the price payload;
- `received_timestamp` = Verge's local/UTC receive time.

Do **not** replace event time with receive time.

## 2.3 Preserve raw payload

Store the original JSON payload.

Reason:

If the RTDS schema changes, you must be able to inspect historical messages instead of reconstructing them from normalized fields.

## 2.4 Add protocol-version awareness

Store:

```text
source
topic
message_type
schema_version
```

If the protocol does not provide a version, use an internal parser version such as:

```text
parser_version = "rtds-v1"
```

## Avoid

- assuming the old DeepSeek subscription syntax is correct;
- assuming every message contains a price;
- assuming `timestamp` is always the price observation time;
- parsing by positional fields;
- silently accepting malformed messages.

## Done When

A real RTDS connection can:

1. subscribe;
2. receive BTC Chainlink updates;
3. parse the payload;
4. store both timestamps;
5. persist the raw message;
6. reject unrelated messages without crashing.

---

# 5. Phase 3 — Build an Always-On Collector

**Goal:** Create the raw data layer that never depends on market boundaries.

The collector must run continuously.

Do **not** start it 30 seconds before a 15-minute market.

The correct architecture is:

```text
RTDS connection
       │
       ├── reconnect automatically
       │
       ├── record every valid observation
       │
       ├── record connection events
       │
       └── expose latest observation
```

## 3.1 Collector responsibilities

The collector should:

- connect to RTDS;
- subscribe to `btc/usd`;
- send keepalive PINGs according to the documented protocol;
- detect disconnects;
- reconnect with bounded backoff;
- assign a connection/session ID;
- persist every valid observation;
- record malformed messages;
- record gaps;
- expose the latest observation to the application.

## 3.2 Backoff

Use bounded exponential backoff, for example:

```text
1s
2s
4s
8s
max 30s
```

Add jitter.

Do not reconnect in a tight infinite loop.

## 3.3 Connection session

Each connection should have:

```text
connection_id
connected_at
disconnected_at
reconnect_count
last_event_timestamp
```

This is necessary for diagnosing missing data.

## 3.4 Gap detection

Calculate:

```text
gap_seconds =
    current_event_timestamp -
    previous_event_timestamp
```

Classify:

```text
0–5s       normal
5–15s      warning
15–60s     degraded
>60s       major gap
```

Do not hardcode these thresholds into settlement logic. They are data-quality classifications.

## 3.5 Clock health

Record:

```text
server_time - event_time
```

for diagnostics.

Use UTC everywhere.

## Avoid

- reconnecting with a brand-new collector state every time;
- throwing away observations after reconnect;
- treating a reconnect as evidence that the market data is continuous;
- using `datetime.now()` as the source event timestamp;
- assuming no message means "price stayed the same" without recording the gap.

## Done When

You can leave the collector running for hours and show:

```text
observations_received
observations_stored
connection_count
largest_gap
last_event_age
```

without manual intervention.

---

# 6. Phase 4 — Add Raw Price Storage in Supabase

**Goal:** Make the raw observation database the source of truth.

Do not use CSV as primary storage.

CSV is an export/debug artifact.

## 4.1 Create `price_snapshots`

Recommended minimum schema:

```sql
CREATE TABLE IF NOT EXISTS price_snapshots (
    id BIGSERIAL PRIMARY KEY,

    source TEXT NOT NULL,
    symbol TEXT NOT NULL,

    event_timestamp TIMESTAMPTZ NOT NULL,
    received_timestamp TIMESTAMPTZ NOT NULL,

    price NUMERIC(24, 10) NOT NULL,

    topic TEXT,
    message_type TEXT,

    connection_id TEXT,
    sequence_id BIGINT,

    raw_payload JSONB,

    parser_version TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## 4.2 Add useful indexes

At minimum:

```sql
CREATE INDEX idx_price_snapshots_source_symbol_time
ON price_snapshots (source, symbol, event_timestamp DESC);

CREATE INDEX idx_price_snapshots_event_time
ON price_snapshots (event_timestamp DESC);

CREATE INDEX idx_price_snapshots_connection
ON price_snapshots (connection_id);
```

If the data volume later becomes large, introduce retention/partitioning deliberately rather than prematurely.

## 4.3 Source values

Use explicit values:

```text
polymarket_chainlink_rtds
chainlink_onchain
binance
coinbase
```

Do not store all of them under `crypto`.

## 4.4 Data-quality fields

Add later if useful:

```text
is_gap_recovery
is_duplicate
is_out_of_order
quality_status
```

These should describe the observation rather than modify the price.

## Avoid

- converting all values to Python floats before storage;
- deleting raw payloads after parsing;
- using a single generic `price` table without a `source`;
- storing only the latest price;
- storing only rounded dollars.

## Done When

A SQL query can retrieve the exact RTDS observation sequence for any historical period.

---

# 7. Phase 5 — Build a Correct Local 60-Second TWAP Engine

**Goal:** Calculate a transparent local reconstruction over the RTDS Chainlink observation stream.

Label it:

```text
chainlink_rtds_twap_estimate
```

not:

```text
official_chainlink_twap
```

until validation proves the equivalence you need.

## 5.1 Define the mathematical contract

For a rolling interval:

```text
[t - 60s, t]
```

the local estimator should integrate the observed price over time.

Conceptually:

```text
TWAP =
Σ(price_i × duration_i)
-----------------------
       window
```

The current observation's price remains effective until the next observation unless the source semantics prove otherwise.

## 5.2 Use event timestamps

Never build the TWAP from receive times.

Use:

```text
event_timestamp
```

for the integral.

Receive time is for monitoring latency.

## 5.3 Handle partial overlap

This is essential.

Suppose:

```text
old observation = 14:00:00
new observation = 14:00:10
rolling cutoff = 14:00:05
```

Only:

```text
14:00:05 → 14:00:10
```

belongs in the current 60-second window.

The calculator must subtract only the expired portion.

## 5.4 Handle out-of-order events

If:

```text
new_event_timestamp < last_event_timestamp
```

do not insert it into the running accumulator blindly.

Options:

1. reject and log it;
2. queue and reorder;
3. recompute the affected window from stored observations.

For correctness, start with:

```text
reject + log
```

and use a periodic exact recomputation path for validation.

## 5.5 Handle duplicates

If the same:

```text
event_timestamp + price + source
```

appears twice, deduplicate it.

Do not silently count it twice.

## 5.6 Handle large gaps

If a 60-second window contains a 90-second data gap:

```text
DO NOT pretend the estimate is fully valid.
```

Return:

```text
quality_status = "degraded"
```

or equivalent.

## 5.7 Keep two calculators

Build:

### Incremental calculator

Fast enough for live signals.

```text
O(1) / amortized O(1)
```

updates.

### Exact replay calculator

Reads the raw 60-second observations from the database and recalculates the window from scratch.

Use it for:

- tests;
- audits;
- validation;
- debugging the incremental calculator.

The two must agree within your defined numerical tolerance.

## 5.8 Numerical representation

Use either:

- Decimal;
- fixed-point integer units.

Do not use binary float as the canonical persisted value.

But remember:

> Numerical precision does not solve source-data differences.

## Avoid

- claiming Decimal makes the calculation "official";
- using only a simplistic `sum(price * delta)` without partial-window handling;
- updating the window based on receive time;
- silently filling major data gaps;
- returning an apparently precise number when the input coverage is incomplete.

## Done When

Unit tests pass for:

- evenly spaced ticks;
- irregular ticks;
- price jumps;
- partial-window overlap;
- duplicate ticks;
- out-of-order ticks;
- exact 60-second boundary;
- data gap;
- reconnection gap.

---

# 8. Phase 6 — Build the 15-Minute Market Window Engine

**Goal:** Separate market timing from price collection.

The collector is always-on.

The market-window engine asks:

> Which observations belong to this Polymarket market?

## 6.1 Market metadata is authoritative

For each 15-minute event, store:

```text
market_id
event_id
slug
window_start
window_end
market_duration
direction
resolution_source
resolution_rules
```

Do not infer market start/end from local wall-clock assumptions if Gamma/event metadata provides authoritative timestamps.

## 6.2 Normalize timestamps

Store:

```text
window_start_utc
window_end_utc
```

as exact UTC timestamps.

Do not allow local timezone values into core calculations.

## 6.3 Associate price observations

For every market:

```text
window_start
window_end
```

query the corresponding raw source observations.

Keep observations that fall inside the required analytical interval.

## 6.4 Pre-market buffer

Because the local 60-second TWAP around the window boundary may require observations immediately before/after the boundary depending on the metric being calculated, the data collector must already be running.

The window engine should support:

```text
buffer_before
buffer_after
```

but never silently change the official market definition.

## 6.5 Market phases

Represent state separately from price:

```text
upcoming
warming
active
closing
closed
resolved
```

Do not define "settled" merely because the local process stopped receiving messages.

## Avoid

- creating a new WebSocket collector per market;
- starting the collector at market open;
- assuming every market is exactly aligned to `00/15/30/45` without checking metadata;
- using receive time to assign observations to a window.

## Done When

Given a market ID, Verge can return the exact:

```text
window_start
window_end
observations_used
observation_count
largest_gap
```

for that window.

---

# 9. Phase 7 — Define Price-to-Beat and Current Reference Correctly

**Goal:** Stop treating "price to beat" and "current price" as generic constants.

They are market-specific analytical states.

## 7.1 Create a reference-state object

Recommended fields:

```json
{
  "market_id": "...",
  "window_start": "...",
  "window_end": "...",

  "reference_source": "polymarket_chainlink_rtds",
  "reference_status": "estimated",

  "price_to_beat": null,
  "price_to_beat_timestamp": null,

  "current_reference": null,
  "current_reference_timestamp": null,

  "final_reference": null,
  "final_reference_timestamp": null,

  "quality_status": "good"
}
```

## 7.2 Do not hardcode P2B semantics

The existing DeepSeek conversation assumed:

```text
price_to_beat = TWAP at open + 60s
```

Do not encode that rule globally.

Instead:

1. read the market's actual resolution rules;
2. verify the market family;
3. identify the strike/reference semantics;
4. encode the rule in a duration-specific strategy/config;
5. test the result against actual markets.

## 7.3 Separate three values

Use explicit names:

```text
price_to_beat
current_reference
final_reference
```

Do not use one generic `current_price`.

## 7.4 Update cadence

The RTDS feed may not produce a new observation every second.

Therefore:

```text
source update cadence
```

and:

```text
Verge UI/API polling cadence
```

must be separate concepts.

Example:

```text
RTDS changes at t=14:02:03.4
Verge API is queried at:
14:02:03
14:02:04
14:02:05
```

The same current reference may be returned between source updates.

That is not bad data.

It is simply the latest known source observation.

## Avoid

- fabricating one-second price updates;
- replacing an unchanged source value with exchange data;
- calling a stale value "current" without age information;
- freezing P2B based on an unverified generic `open + 60s` rule.

## Done When

The API can clearly answer:

```text
What is the reference now?
When was it observed?
Which source produced it?
What is the locked price-to-beat?
How was it obtained?
How fresh is it?
```

---

# 10. Phase 8 — Add Multi-Source Comparison

**Goal:** Turn the free-source limitation into a measurable research dataset.

Store simultaneously:

```text
Polymarket RTDS Chainlink
local reconstructed 60s TWAP
on-chain Chainlink
Binance
Coinbase
```

## 8.1 Calculate pairwise spreads

For every synchronized timestamp:

```text
rtds_vs_local_twap
rtds_vs_onchain
rtds_vs_binance
rtds_vs_coinbase
```

Store:

```text
absolute_difference
percentage_difference
timestamp_delta
```

## 8.2 Measure lag

For each source:

```text
source timestamp
receive timestamp
```

Calculate:

```text
latency_ms
```

This will later answer whether differences are price differences or timing differences.

## 8.3 Add volatility context

Store a basic volatility regime:

```text
low
normal
high
extreme
```

or a numerical volatility measure.

Later ask:

> Does the approximation error increase during volatile periods?

## 8.4 Do not use comparisons to choose the "winner" live yet

At first the comparison engine is observational.

Do not say:

```text
Binance is closer today → use Binance as resolution source.
```

The comparison logic should collect evidence before altering live behavior.

## Avoid

- selecting a free source based on a handful of windows;
- using correlation as proof of settlement equivalence;
- replacing RTDS with Binance because it "looks smoother";
- hiding source divergence.

## Done When

A historical query can produce:

```text
source
mean absolute error
median absolute error
p95 error
max error
mean latency
p95 latency
gap count
```

by market and by volatility regime.

---

# 11. Phase 9 — Build the Validation Dataset

**Goal:** Determine empirically whether the free architecture is good enough for Verge.

This phase is mandatory before using the local estimate as a settlement proxy.

## 9.1 Record every market, not only trades

For every 15-minute BTC market:

```text
market_id
window_start
window_end
resolution_rules
resolution_source
final Polymarket outcome
```

Do not limit the dataset to markets where Verge generated a bet.

Otherwise the dataset becomes selection-biased.

## 9.2 Record all candidate references

For each market:

```text
P2B estimate
current RTDS reference
local TWAP estimate
on-chain Chainlink
Binance
Coinbase
```

## 9.3 Record the final observed market outcome

Use the same outcome-resolution infrastructure already being developed in `window_outcomes`.

Store:

```text
UP
DOWN
```

plus:

```text
resolved_at
resolution method/source
```

## 9.4 Measure error

For every market, calculate:

```text
reference_error =
    candidate_final_price -
    best_available_resolution_reference
```

Where the "best available resolution reference" must be explicitly documented.

Do not silently use an approximation and call it ground truth.

## 9.5 Define acceptance thresholds before looking at results

For example:

```text
Target A:
median absolute price error < X

Target B:
95th percentile absolute error < Y

Target C:
direction agreement with official outcome >= Z%

Target D:
gap rate < G%
```

The values X/Y/Z/G should be chosen before the validation report is interpreted.

## 9.6 Run at least hundreds of windows

Do not judge the system from:

```text
5 markets
20 markets
one volatile day
```

The goal is a dataset large enough to include:

- calm periods;
- high-volatility periods;
- rapid BTC moves;
- reconnects;
- source divergences.

## Avoid

- calling the architecture accurate before validation;
- choosing metrics after seeing the results;
- excluding bad windows because "the feed was weird";
- counting only successful collector runs.

## Done When

You have a report answering:

> How close does the free RTDS/local reconstruction come to the best available Polymarket resolution reference?

and:

> Does it correctly predict UP/DOWN often enough for Verge?

---

# 12. Phase 10 — Build a Data-Quality/Confidence Layer

**Goal:** Never allow Verge to treat degraded data as normal data.

Create a quality object such as:

```json
{
  "status": "good",
  "source_age_ms": 420,
  "observations_last_60s": 14,
  "largest_gap_ms": 3200,
  "reconnects": 0,
  "out_of_order_count": 0,
  "duplicate_count": 0,
  "coverage_ratio": 0.998
}
```

## Status levels

Suggested:

```text
good
degraded
stale
invalid
```

## Example logic

### GOOD

- fresh source;
- full 60s coverage;
- no major gaps;
- normal parser state.

### DEGRADED

- minor gap;
- reconnect occurred;
- lower-than-usual observation count.

### STALE

- latest observation too old.

### INVALID

- malformed price;
- impossible timestamp;
- insufficient data to calculate requested reference.

## Integrate with signal engine

A 15-minute signal should be able to say:

```text
signal_quality = degraded
```

rather than pretending the reference is perfect.

## Avoid

- returning a number without a quality flag;
- forcing `0` on missing price;
- silently substituting Binance when RTDS fails;
- turning degraded data into normal data.

## Done When

The dashboard/API can explain not only:

```text
current_reference = $X
```

but:

```text
current_reference = $X
quality = degraded
reason = RTDS gap of 18.4s
```

---

# 13. Phase 11 — Integrate with Verge's 15-Minute Signal Engine

**Goal:** Make the new layer useful without allowing it to contaminate existing behavior.

## 11.1 Add source-aware fields to the signal

Recommended:

```text
reference_price
reference_source
reference_status
reference_age_ms
price_to_beat
distance_from_price_to_beat
distance_pct
time_remaining
```

## 11.2 Preserve existing indicators

Keep:

```text
RSI
MA
Volume
Polymarket odds
score
edge
```

as separate signals.

Do not rewrite them around the new TWAP engine.

## 11.3 Add a reference-direction feature

Example:

```text
reference_direction = UP
```

when:

```text
current_reference > price_to_beat
```

and:

```text
reference_direction = DOWN
```

when below.

Tie logic to the actual verified market rule.

## 11.4 Add divergence feature

Example:

```text
reference_divergence =
    local_twap -
    rtds_reference
```

Initially this should be a diagnostic feature.

Only promote it into the score after validation proves that it has predictive value.

## 11.5 Keep signal and resolution separate

This is critical.

The system can use:

```text
Binance momentum
RTDS reference
RSI
volume
odds
```

to make a prediction.

But the final outcome should still be determined by the market's verified resolution rules.

Do not make:

```text
signal == settlement
```

## Avoid

- letting a source-quality failure crash the signal engine;
- changing the existing score weights before backtesting;
- using P2B crossing alone as the predictive model;
- assuming "above P2B" means a high-probability win without measuring it historically.

## Done When

A 15m signal can show:

```text
Decision: BET HIGHER
Reference: $67,218.42
Price to Beat: $67,201.90
Difference: +$16.52 (+0.0245%)
Reference Source: Polymarket Chainlink RTDS
Reference Quality: GOOD
```

while all existing signal components remain visible.

---

# 14. Phase 12 — Add Live API + Frontend Observability

**Goal:** Make the reference system inspectable while it is running.

## API endpoints

Add a new endpoint, for example:

```text
GET /api/price-reference?duration=15m
```

Response:

```json
{
  "market": {
    "id": "...",
    "window_start": "...",
    "window_end": "..."
  },
  "reference": {
    "source": "polymarket_chainlink_rtds",
    "price": 67218.42,
    "observed_at": "...",
    "received_at": "..."
  },
  "twap_estimate": {
    "window_seconds": 60,
    "value": 67217.91,
    "status": "good"
  },
  "price_to_beat": {
    "value": 67201.90,
    "captured_at": "..."
  },
  "quality": {
    "status": "good",
    "largest_gap_ms": 3200
  }
}
```

## Frontend

Show:

```text
LIVE REFERENCE
$67,218.42

LOCAL 60S ESTIMATE
$67,217.91

PRICE TO BEAT
$67,201.90

DIFFERENCE
+$16.52

SOURCE
Polymarket / Chainlink RTDS

QUALITY
GOOD

LAST UPDATE
420 ms ago
```

Also show:

```text
RTDS vs Local
RTDS vs On-chain
RTDS vs Binance
```

in a diagnostic view.

## Avoid

- showing estimates without labels;
- calling the local TWAP "official";
- hiding source age;
- mixing diagnostic/reference fields with actual trading odds.

## Done When

You can visually inspect the entire price-reference state from the Verge dashboard.

---

# 15. Phase 13 — Reliability and Failure Simulation

**Goal:** Prove the system behaves correctly when real systems fail.

Test:

### WebSocket

- disconnect for 1 second;
- disconnect for 30 seconds;
- repeated disconnects;
- malformed message;
- subscription rejection;
- delayed message.

### Database

- temporary write failure;
- slow write;
- duplicate insert;
- unavailable Supabase.

### Time

- local clock drift;
- out-of-order timestamps;
- duplicate timestamps;
- large timestamp jump.

### Market

- market metadata missing;
- market appears late;
- market closes while disconnected;
- collector reconnects after market close.

## Expected behavior

The system should:

1. retain already-collected data;
2. flag the missing interval;
3. reconnect;
4. avoid inventing observations;
5. prevent invalid TWAP from being labeled `good`;
6. allow exact replay from persisted raw data.

## Avoid

- "fill forward forever";
- "use current Binance price until RTDS returns";
- resetting the entire collector on one bad packet;
- deleting corrupted rows;
- declaring a final value from an incomplete window without a quality flag.

---

# 16. Phase 14 — Historical Replay Engine

**Goal:** Be able to reproduce any market's reference calculations from stored raw data.

Create:

```text
replay_market(market_id)
```

It should:

1. load raw observations;
2. sort and validate them;
3. reconstruct the local TWAP timeline;
4. compute P2B/reference states;
5. produce the same outputs that the live engine produced;
6. produce a deterministic report.

## Why this matters

When Verge eventually sees:

```text
Polymarket resolved UP
Verge reference predicted DOWN
```

you need to answer:

> What exactly did Verge see at the time?

without relying on memory or current live APIs.

## Replay output

Store/export:

```text
market_id
source observations
TWAP estimates
P2B estimate
quality states
final reference
resolution outcome
```

## Done When

A historical market can be replayed offline with identical results.

---

# 17. Phase 15 — Validation Dashboard / Research Report

**Goal:** Make source quality measurable.

Create reports such as:

## Report A — Source Accuracy

```text
Source                     MAE      Median     P95
---------------------------------------------------
RTDS reference             ...
Local RTDS TWAP            ...
On-chain Chainlink         ...
Binance                    ...
Coinbase                   ...
```

## Report B — Outcome Agreement

```text
Reference                     UP/DOWN agreement
------------------------------------------------
RTDS reference                ...
Local TWAP                    ...
On-chain Chainlink            ...
Binance                       ...
```

## Report C — Market Condition

Compare errors during:

```text
low volatility
normal volatility
high volatility
extreme volatility
```

## Report D — Data Quality

```text
markets observed
markets fully captured
markets with gaps
markets with reconnects
markets with malformed data
```

## Report E — P2B timing

Measure:

```text
candidate capture timestamp
observation nearest target
latency
difference from alternative capture points
```

This is where the repository's existing window-outcome and observation work becomes particularly valuable.

---

# 18. Phase 16 — Only After Validation: Promote a Reference

**Goal:** Decide what Verge should treat as its primary free reference.

Do not decide this today.

Decide it after the validation dataset.

Possible outcomes:

### Outcome A — RTDS is sufficiently reliable

Use:

```text
polymarket_chainlink_rtds
```

as primary free reference.

### Outcome B — local TWAP consistently improves agreement

Use:

```text
chainlink_rtds_twap_estimate
```

as the primary *estimate*, but keep the source label explicit.

### Outcome C — on-chain Chainlink is surprisingly close

Keep it as a fallback/reference.

### Outcome D — no free method is sufficiently reliable

Keep the system as:

```text
signal reference + probabilistic estimate
```

rather than pretending to have settlement-level certainty.

### Outcome E — direct Chainlink access becomes available

Add it as:

```text
official_reference
```

without redesigning the rest of the system.

That is why the architecture must be source-agnostic.

---

# 19. Recommended Database Model

Do not force everything into `window_observations`.

Use separate conceptual layers.

## `price_snapshots`

Raw source observations.

```text
source
symbol
event_timestamp
received_timestamp
price
raw_payload
connection_id
quality metadata
```

## `window_observations`

Continuous Verge signal snapshots.

```text
market_duration
market_window_start
seconds_into_window
odds
current_price
reference_price
price_to_beat
indicators
score
decision
```

## `window_outcomes`

Ground truth outcome.

```text
market_duration
market_window_start
actual_outcome
resolved_at
resolution_source
```

## `price_reference_snapshots`

Derived/reference state.

```text
market_duration
market_window_start
timestamp
rtds_price
local_twap
onchain_chainlink
binance_price
coinbase_price
quality_status
```

This separation keeps raw data, derived data, signal data, and ground truth independent.

---

# 20. Recommended File Structure

A practical target:

```text
backend/
├── app.py
├── engine.py
├── market_config.py
│
├── polymarket_fetcher.py
├── polymarket_rtds.py              # NEW
│
├── chainlink_fetcher.py
├── data_fetcher.py
│
├── twap.py                         # NEW
├── price_reference.py              # NEW
├── market_window.py                # NEW
├── price_quality.py                # NEW
│
├── data_alignment.py
├── backtest.py
├── report.py
├── db.py
│
└── tests/
    ├── test_rtds.py                # NEW
    ├── test_twap.py                # NEW
    ├── test_price_reference.py     # NEW
    ├── test_market_window.py       # NEW
    ├── test_price_quality.py       # NEW
    └── fixtures/
        └── rtds/
```

---

# 21. What NOT to Do

This section is intentionally blunt.

## Do not claim exactness

Never write:

```text
"official TWAP"
"100% accurate"
"99.9% identical"
"exactly what Polymarket uses"
```

unless the implementation has been independently validated to that standard.

## Do not use Binance as a silent fallback for resolution

Binance is excellent for:

- momentum;
- volume;
- technical analysis;
- comparison.

It is not a substitute for the documented resolution source.

## Do not calculate market timing from receive time

Use:

```text
source event timestamp
```

for analytical windows.

Use:

```text
receive timestamp
```

for latency monitoring.

## Do not start the collector per market

Keep the source collector alive continuously.

The market engine should slice the continuous stream into windows.

## Do not make CSV your truth

Use:

```text
Supabase/Postgres = canonical history
CSV = export/debug
JSON = live cache
```

## Do not hide data gaps

A missing interval is information.

Store the gap.

## Do not fabricate one-second prices

If the source did not send a new observation, Verge should not pretend that it received one.

It may expose the latest known value to the UI every second, but the source timestamp must remain unchanged.

## Do not let a proxy look official

If Binance volume is used as a proxy, label it:

```text
volume_source = "binance_proxy"
```

If local TWAP is a reconstruction, label it:

```text
reference_status = "estimated"
```

## Do not change the scoring model during data-source validation

First validate the price layer.

Then test whether the new information improves prediction.

Otherwise you cannot tell which change helped.

---

# 22. Definition of Done

This build is **not complete** just because the WebSocket connects.

The entire plan is complete when all of the following are true:

- [ ] RTDS `btc/usd` Chainlink-source collector is running continuously.
- [ ] Raw payloads are persisted.
- [ ] Event and receive timestamps are both preserved.
- [ ] Reconnects are logged.
- [ ] Data gaps are detectable.
- [ ] Duplicates and out-of-order observations are handled.
- [ ] Local 60-second TWAP reconstruction passes deterministic tests.
- [ ] Exact replay calculation agrees with incremental calculation.
- [ ] Market windows are linked to authoritative market metadata.
- [ ] P2B semantics have been verified for the target market family.
- [ ] Current reference and final reference are separate concepts.
- [ ] Source labels are explicit.
- [ ] Quality flags are exposed.
- [ ] On-chain Chainlink, Binance, and Coinbase comparisons are stored.
- [ ] Every 15-minute window gets a ground-truth outcome record.
- [ ] Hundreds of historical windows have been collected.
- [ ] A validation report measures source discrepancy.
- [ ] Historical markets can be replayed offline.
- [ ] The 15-minute signal engine consumes the reference layer safely.
- [ ] The 1-hour signal path remains unchanged.
- [ ] No component claims that the local estimate is officially identical without evidence.
- [ ] The system can continue operating through temporary RTDS outages without silently fabricating data.

---

# 23. Suggested Build Order

Do **not** implement all phases at once.

Use this order:

```text
1. Freeze 1h behavior
        ↓
2. Verify RTDS protocol
        ↓
3. Build always-on RTDS collector
        ↓
4. Persist raw observations
        ↓
5. Build/test TWAP engine
        ↓
6. Build market-window engine
        ↓
7. Add P2B/current/final reference state
        ↓
8. Add multi-source comparisons
        ↓
9. Collect historical validation data
        ↓
10. Build quality layer
        ↓
11. Build replay engine
        ↓
12. Analyze validation results
        ↓
13. Integrate reference into 15m signals
        ↓
14. Expose API/dashboard
        ↓
15. Only then promote a primary free reference
```

The key principle is:

> **Collect first. Validate second. Integrate third. Optimize last.**

---

# 24. First Coding Session — Exact Tasks

Start with only these tasks.

### Task 1

Create:

```text
backend/polymarket_rtds.py
```

Responsibilities:

```text
connect()
subscribe()
receive()
parse()
reconnect()
close()
```

### Task 2

Create:

```text
backend/tests/fixtures/rtds/
```

Capture real RTDS BTC Chainlink messages.

### Task 3

Create:

```text
backend/tests/test_rtds.py
```

Test:

```text
valid message
wrong topic
wrong symbol
missing price
missing timestamp
malformed JSON
duplicate message
```

### Task 4

Add raw snapshot persistence to Supabase.

Do not calculate TWAP yet.

### Task 5

Run the collector continuously.

Do not connect it to the signal engine yet.

### Task 6

Let it accumulate raw data.

Only once the raw stream is proven stable should you build the TWAP layer.

---

# 25. Golden Rule for Verge

The most important architectural rule for this entire feature is:

```text
RAW OBSERVATION
      ↓
DERIVED REFERENCE
      ↓
QUALITY ASSESSMENT
      ↓
SIGNAL
      ↓
OUTCOME
```

Never collapse those layers into one number.

A future Verge developer should be able to answer:

> "What did we receive?"

then:

> "What did we calculate?"

then:

> "How confident were we?"

then:

> "What signal did we produce?"

then:

> "What did the market actually resolve to?"

That audit trail is more valuable than pretending the free feed is something it has not yet been proven to be.

---

# References

Polymarket RTDS documentation:  
https://docs.polymarket.com/market-data/websocket/rtds

Verge repository:  
https://github.com/er4l1m4d/verge

Existing Verge 15-minute build plan:  
https://github.com/er4l1m4d/verge/blob/master/BUILD_PLAN_15MIN_MARKETS.md

Existing Verge live-price feed plan:  
https://github.com/er4l1m4d/verge/blob/master/BUILD_PLAN_LIVE_PRICE_FEED.md

Existing Verge observation logging plan:  
https://github.com/er4l1m4d/verge/blob/master/BUILD_PLAN_WINDOW_OBSERVATIONS.md

Existing Verge window-outcome comparison plan:  
https://github.com/er4l1m4d/verge/blob/master/BUILD_PLAN_WINDOW_OUTCOMES_COMPARISON.md

Chainlink BTC/USD Data Streams reference should remain verified directly against the current market's resolution rules before making any production claim about settlement equivalence.
