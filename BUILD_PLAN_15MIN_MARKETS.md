# Build Plan: 15-Minute Market Support (Addendum)

Companion to `BUILD_PLAN_SignalTracker.md`, `PRD_SignalTracker.md`, and
`DESIGN.md`. This adds 15-minute BTC signals **alongside** the existing
1-hour path — nothing here should change 1-hour behavior. 11 phases.

Every external link and address below was pulled directly from Polymarket's
and Chainlink's own pages during planning, not from memory. Where something
couldn't be confirmed that way, it's marked **UNVERIFIED** with exactly
what to go search for — don't treat those as fact until you've checked.

---

## Phase 1 — Groundwork & Verification ✅ COMPLETE

**Status:** All items verified August 7, 2026.
See `PHASE1_VERIFICATION.md` for full details.

**1.1 — Confirm the 15-minute market's actual resolution source**
**CONFIRMED.** Live market Rules section reads: *"The resolution source for
this market is information from Chainlink, specifically the BTC/USD TWAP data
stream available at https://data.chain.link/streams/btc-usd-twcap-60s-streams."*
Polymarket uses Chainlink Data Streams (TWAP 60s), not the on-chain Data Feed.
The on-chain Data Feed is accepted as a close approximation for v1 — same oracle
network, 0.1% deviation threshold. See 3.1 for documentation of this gap.

**1.2 — Confirm the Chainlink on-chain feed address**
**CONFIRMED.** Polygon Mainnet BTC/USD feed: `0xc907E116054Ad103354f2D350FD2514433D57F6f`
(verified via Chainlink feed explorer + Polygonscan). 8 decimal places,
0.1% deviation threshold, 17 oracle operators.

**1.3 — Confirm the 15-minute event slug pattern**
**CONFIRMED.** Pattern: `btc-updown-15m-{unix_timestamp}`.
Example: `btc-updown-15m-1786193100`. Other assets follow same shape
(`eth-updown-15m-...`, `sol-updown-15m-...`).

**1.4 — Confirm the Gamma API series_slug for 15-minute markets**
**CONFIRMED.** Value: `btc-up-or-down-15m`.
Queried several candidates; only this one returns active events.
Note: event slug prefix is shorter (`btc-updown-15m-`) than series slug.

**1.5 — Decide the RPC endpoint for reading the Chainlink feed**
**DECIDED.** Primary: `polygon-bor-rpc.publicnode.com` (publicnode.com).
Fallback: `https://polygon-rpc.com` (deprecated, needs API key).
Public endpoint tested and confirmed working (block #91,602,108).

---

## Phase 2 — Market Configuration Layer ✅ COMPLETE

**Status:** Implemented August 7, 2026.
New module: `backend/market_config.py`

**2.1 — Build a per-duration config structure**
**DONE.** Created `market_config.py` with `MARKET_CONFIG` dict containing
one entry per duration ("1h" and "15m"). Each entry includes: window_ms,
series_slug, slug_prefix, price_source, bar_interval, bar_lookback,
rsi_period, ma_fast, ma_slow, volume_lookback, no_bet_final_minutes,
suggested_price_discount, min_candles. The "1h" entry reproduces the exact
hardcoded values from indicators.py and engine.py — verified with 10-point
automated check, all passed. PRD-locked constants (weights, thresholds, fees)
included as module-level constants. `get_config()` and `supported_durations()`
helpers provided.

**2.2 — Size the 15-minute entry's indicator windows correctly**
**DONE.** The 15m entry uses 1-minute bars so:
- RSI-14 covers 14 minutes (fits inside the 15m window, not pre-window data)
- MA-5 covers 5 minutes (short-term trend within the window)
- MA-15 covers exactly 15 minutes (the full window length)
- no_bet_final_minutes = 3 (~20% of window, proportionally tighter than 1h's ~17%)
- Volume proxied from Binance (Phase 3.4)

**2.1 — Build a per-duration config structure**
How: A new module, `market_config.py`, holding one entry per duration with:
window length in ms, the Gamma `series_slug` (from 1.4, or `None` if
relying on slug-prefix only), the slug-prefix fallback pattern (from 1.3),
which price source it resolves against (`"binance"` or `"chainlink"`), the
bar interval indicators should run on, and duration-scaled versions of the
RSI period / MA fast-slow windows / volume lookback / no-bet-final-minutes
rule from the original PRD. The existing 1-hour behavior becomes this
module's `"1h"` entry — verify it reproduces the current hardcoded values
exactly (RSI 14, MA 5/15 on 5-minute bars, volume lookback 10, no-bet
final 10 minutes) so nothing shifts under the trades already accumulating.
Done when: importing this config for `"1h"` and comparing every field
against what's currently hardcoded in `indicators.py` and `engine.py`
shows no differences.

**2.2 — Size the 15-minute entry's indicator windows correctly**
How: This is the one place a straight proportional scale-down is wrong.
The 1-hour path deliberately runs indicators on 5-minute bars, so its
15-period RSI and 15-bar slow MA look back roughly an hour and 75 minutes
respectively — already longer than the market itself, which the PRD
accepted as an intentional tradeoff. For a 15-minute market, carrying that
same stretch forward would mean the indicators are almost entirely reading
stale, pre-window data. Instead: run 15-minute indicators on **1-minute**
bars — a 14-period RSI then covers 14 minutes (fits inside the window), a
15-bar slow MA covers exactly the window length, and a 5-bar fast MA
covers 5 minutes. Set the no-bet-final-minutes rule to something
proportionally tighter too (e.g. 2-3 minutes, not 10).
Done when: you can explain, in one sentence, why each 15-minute indicator
window was chosen — not just that it was scaled by some fraction of the
1-hour values.

---

## Phase 3 — Chainlink Price Source ✅ COMPLETE

**Status:** Implemented August 7, 2026.
New module: `backend/chainlink_fetcher.py`. Added `web3>=7,<8` to requirements.txt.

**3.1 — Understand and document the approximation you're accepting**
**DONE.** Full documentation block in chainlink_fetcher.py explaining:
Data Streams TWAP 60s (actual resolution) vs on-chain Data Feed (what we read),
why the gap exists, why it's acceptable (same oracle network, 0.1% deviation),
and how it compares to the existing CoinGecko-vs-Binance mismatch.

**3.2 — Build the on-chain reader**
**DONE.** `get_chainlink_price()` reads latestRoundData() from contract
`0xc907E116054Ad103354f2D350FD2514433D57F6f` via web3.py against RPC
`polygon-bor-rpc.publicnode.com`. Returns price as float (divides by 10^8).
Handles: connection failures, stale data warnings (>1hr), non-positive prices.
Verified: returns real BTC price ($64,815.57).

**3.3 — Decide how to build indicator bars without historical backfill**
**DONE.** `resample_to_bars()` builds 1-minute OHLC from accumulated price ticks.
`get_chainlink_bars()` orchestrates: (1) resample cached ticks, (2) if not enough,
bootstrap from Binance 1m candles. Warm-up period accepted; Binance bootstrap
eliminates it on Render where Binance is accessible.

**3.4 — Solve the volume gap**
**DONE.** Volume is proxied from Binance BTC/USDT 1m candles during bootstrap.
Chainlink ticks have volume=0. The bootstrap path provides real market volume.
Signal notes will label volume as "Binance proxy" for 15m signals.

**3.1 — Understand and document the approximation you're accepting**
How: This is a documentation task, not a code task, and it matters. Write
a short comment block (in the module from 3.2) stating plainly: the actual
resolution source is Chainlink Data Streams (low-latency, typically needs
a paid subscription to query directly), while what this code reads is
Chainlink's older on-chain Data Feed product — same oracle network and a
tight 0.1% deviation threshold, but not guaranteed tick-identical to the
literal resolution feed. This is the same category of gap as the original
CoinGecko-vs-Binance mismatch this project already caught once — the goal
here isn't to eliminate it (there's no free way to), it's to make sure
nobody forgets it's there.
Done when: this caveat exists in writing somewhere a future you (or a
future paper-trading result review) will actually see it.

**3.2 — Build the on-chain reader**
How: A new module, e.g. `chainlink_fetcher.py`, using the `web3.py`
library (add `web3` to `requirements.txt`) against the RPC endpoint from
1.5 and the contract address from 1.2. You only need two contract
functions from Chainlink's standard `AggregatorV3Interface` —
`latestRoundData()` (returns price, round id, and timestamps) and
`decimals()` (BTC/USD feeds typically use 8 decimal places, but read it
from the contract rather than hardcoding it). Reference implementation
pattern (Solidity, but shows the interface and decimal handling):
[https://docs.chain.link/data-feeds/using-data-feeds](https://docs.chain.link/data-feeds/using-data-feeds).
You don't need Solidity — `web3.py`'s `contract.functions.latestRoundData().call()`
does the same read from Python. Wrap the whole thing in a try/except
returning `None` on failure — RPC calls fail sometimes, and this needs to
degrade gracefully, not crash the heartbeat.
Done when: calling this function returns a live BTC price that's sane
(within a percent or two of what Binance or any exchange shows right now).

**3.3 — Decide how to build indicator bars without historical backfill**
How: Unlike Binance, there's no free historical range query for this
feed — you can't ask for "the last 90 days of 1-minute bars." The
practical path: record a price tick to your database on every heartbeat
call, and build 1-minute OHLC bars by resampling the accumulated ticks
in memory each time a signal is generated. This means there's a genuine
warm-up period — the 15-minute path won't have enough history to compute
a real signal until it's been running for a while (tens of minutes at
minimum, more before the slow MA is fully warmed up).
Done when: you've accepted this tradeoff explicitly rather than
discovering it as a surprise once deployed — there is no faster free
option here.

**3.4 — Solve the volume gap**
How: A price feed carries no trading volume — there's no Chainlink analog
to "volume spiked 3x." Recommended default: proxy the volume signal from
Binance's real BTC/USDT volume over the same wall-clock window. It's not
volume on the feed this market actually resolves against, but it's real
market activity that's usually a reasonable cross-market indicator of
momentum, and it's free and already available from the existing Binance
integration. Whatever you choose, surface it explicitly in the signal's
notes field — don't let a proxied number look identical to a real one.
Done when: the volume component of a 15-minute signal is populated from
some defensible source, and it's visibly labeled as a proxy wherever the
signal is displayed or logged.

---

## Phase 4 — Market Discovery, Generalized

**Goal:** find the live 15-minute BTC market the same reliable way the
1-hour path already does, with a fallback in case 1.4 never got resolved.

**4.1 — Generalize market discovery by duration**
How: The existing discovery function queries Gamma's `/events` endpoint
filtered by `series_slug`. Generalize it to accept a duration, look up
that duration's `series_slug` from the Phase 2 config, and run the same
query. If `series_slug` is `None` (1.4 unresolved) or the query returns
nothing, fall back to a second method (4.2).
Done when: calling this with `"1h"` behaves identically to the current
code, byte for byte in terms of what gets returned.

**4.2 — Build the slug-prefix fallback**
How: Pull a broader page of recent events from Gamma (no series filter,
just sorted by start date, a reasonably high limit like 100) and filter
client-side for slugs starting with the duration's confirmed prefix from
1.3 (`btc-updown-15m-`), then apply the same "currently active" time-window
check the primary method uses. This makes discovery work correctly even
if 1.4's series_slug guess turns out wrong.
Done when: temporarily forcing the primary method to fail (e.g. passing a
deliberately wrong series_slug) still results in the correct live
15-minute market being found via this fallback.

---

## Phase 5 — Signal Generation, Generalized

**Goal:** the same scoring pipeline the 1-hour path already validated, now
duration-aware end to end.

**5.1 — Thread duration through the signal generation function**
How: The core signal function should accept a duration parameter
(defaulting to `"1h"` so nothing calling it without one changes behavior),
pull that duration's config from Phase 2, and use it to decide: which
market-discovery method to call (Phase 4), which price source to pull from
(Binance, unchanged, or Chainlink via Phase 3), and which RSI/MA/volume
window sizes to pass into the existing scoring functions — those functions
already accept these as parameters, so no changes needed there.
Done when: generating a signal for `"1h"` and for `"15m"` both produce
complete, correctly-shaped results using their respective price sources.

**5.2 — Apply the no-bet-final-minutes rule per duration**
How: Pull the duration-specific value from Phase 2's config (e.g. 10
minutes for 1h, 2-3 for 15m) rather than a single hardcoded constant.
Done when: a signal generated in the closing minutes of a 15-minute window
downgrades to SKIP at the right threshold, not the 1-hour one.

---

## Phase 6 — Persistence, Migrated

**Goal:** 1-hour and 15-minute trades tracked as genuinely separate
records, not blended into one number.

**6.1 — Add a duration column to both trade tables**
How: `ALTER TABLE signals ADD COLUMN market_duration TEXT NOT NULL DEFAULT '1h';`
and the same for `paper_trades`. The default backfills every existing row
as `'1h'`, which is correct — they all were.
Done when: querying either table shows every existing row correctly
labeled `'1h'`, with no nulls.

**6.2 — Add a price-tick table for the Chainlink bar-builder**
How: A new table, e.g. `price_snapshots` (source, symbol, timestamp,
price) — the raw material Phase 3.3's resampling reads from. Append-only,
one row per heartbeat tick.
Done when: after a few heartbeat cycles, this table is visibly
accumulating rows.

**6.3 — Make the write/read paths duration-aware**
How: Every write to `signals`/`paper_trades` now includes
`market_duration`. The idempotency check (does a signal already exist for
this window) should scope by duration as well as window start, not just
window start alone. Stats queries and the unresolved-trades lookup should
accept an optional duration filter — pass one to scope to a single
duration's numbers, omit it for a combined view.
Done when: generating signals for both durations in the same test run
produces two cleanly separated sets of rows, and querying stats with a
duration filter shows only that duration's trades.

**6.4 — Split the graduation gate per duration**
How: The PRD's graduation gate (`count >= 200 AND cumulative_roi > 0`)
should be computed **separately** for each duration once this ships — a
15-minute market is a different enough structure (much less time for an
edge to play out, same punishing fee curve) that it needs to prove itself
independently rather than borrowing credibility from the 1-hour path's
trade count.
Done when: the stats endpoint can report `unlock_real_orders` for `"1h"`
and `"15m"` separately, and they're currently different values (since one
has real accumulated history and the other is starting from zero).

---

## Phase 7 — Resolution, Generalized

**Goal:** correctly determine what actually happened, using each
duration's real resolution method.

**7.1 — Keep the 1-hour resolution path exactly as-is**
How: Binance candle open/close comparison for the window, unchanged.
Done when: no behavior change, confirmed by re-running whatever validation
you did originally for this path.

**7.2 — Build the Chainlink-tick resolution path for 15-minute**
How: Since there's no historical range query (Phase 3.3), resolution for
a 15-minute window has to use whatever price ticks were actually recorded
during that window: the earliest tick at/after the window's start
approximates the open price, the latest tick at/before the window's close
approximates the close price. If no ticks exist yet for a window that
should have closed (e.g. it resolved before enough history accumulated),
leave the trade unresolved and retry on the next heartbeat rather than
guessing.
Done when: a 15-minute paper trade that ran during a period with enough
recorded ticks resolves correctly and matches the actual Up/Down outcome
shown on Polymarket for that window.

---

## Phase 8 — API & Heartbeat, Generalized

**Goal:** one heartbeat call that keeps both durations running.

**8.1 — Add duration as a query parameter**
How: Signal and stats endpoints accept `?duration=1h` (default) or
`?duration=15m`.
Done when: both values return correctly scoped, correctly shaped
responses.

**8.2 — Make the heartbeat run every configured duration**
How: Instead of running one signal-generate-persist-alert-resolve cycle,
loop over every duration in the Phase 2 config and run the full cycle for
each, independently — one duration failing (e.g. a Chainlink RPC hiccup)
shouldn't block the other from completing. Report each duration's outcome
separately in the response.
Done when: hitting the heartbeat endpoint once produces both a 1-hour and
a 15-minute result in a single response, and temporarily breaking one
(e.g. an invalid RPC URL) doesn't stop the other from working.

---

## Phase 9 — Scheduler & Alerts

**Goal:** the operational pieces that make the 15-minute path actually
useful day to day, not just correct in isolation.

**9.1 — Move to a scheduler that supports 1-minute intervals**
How: The 15-minute path's indicators run on 1-minute bars, so a 5-minute
heartbeat leaves it working from thin history most of the time.
UptimeRobot's free tier floors at 5-minute checks. **cron-job.org**
([https://cron-job.org](https://cron-job.org)) supports 1-minute intervals
on its free tier — point it at the heartbeat URL instead, or run it
alongside UptimeRobot if you want the redundancy.
Done when: heartbeat hits are landing roughly every minute, confirmed in
your backend logs or the price_snapshots table's timestamp spacing.

**9.2 — Label Telegram alerts by duration**
How: Since the same bot now fires for two different market structures,
every alert needs to say which one — direction and duration side by side
in the message header, not just direction.
Done when: a burst of alerts from both durations arriving close together
is still unambiguous at a glance.

---

## Phase 10 — Frontend

**Goal:** DESIGN.md's dashboard, now showing two markets without becoming
two disconnected pages.

**10.1 — Add a duration toggle**
How: A small switch or tab control near the status band — "1H / 15M" —
that re-points the dashboard's polling at `?duration=15m` and re-renders
the same decision/evidence/probability/history bands against that
duration's data. Reuse every existing component; only the data source
changes.
Done when: switching the toggle updates every band correctly, and the
countdown timer reflects the selected duration's actual window length
(15 minutes, not 60).

**10.2 — Distinguish the approximated data source in the UI**
How: When viewing the 15-minute dashboard, surface Phase 3.1's caveat
somewhere visible but unobtrusive — e.g. a small label near the price
readout noting the price is Chainlink-approximated and volume is
Binance-proxied. This isn't disclaimers-for-their-own-sake; it's the kind
of thing you'll want to see at a glance six months from now when deciding
how much to trust a run of signals.
Done when: the distinction is visible without needing to check code or
documentation to remember it's there.

---

## Phase 11 — Validation

**Goal:** the 15-minute path earns trust the same way the 1-hour path had
to — not by inheriting it.

**11.1 — Backtest using Binance data as a stand-in**
How: True historical backtesting against the actual Chainlink feed isn't
free (no historical range API — see 3.3). A reasonable interim substitute:
run the same directional backtest approach from the original build plan,
but against Binance's free 1-minute historical klines as an approximation
of the Chainlink feed's behavior. Document clearly in the results that
this tests a correlated-but-different feed, not the literal resolution
source — same spirit as the live volume proxy.
Done when: you have a backtest report for the 15-minute strategy, with
this caveat stated in the report itself, not just in your head.

**11.2 — Run an independent paper-trading window**
How: Let the 15-minute path accumulate its own paper trades from zero,
tracked separately per Phase 6.4's split graduation gate. Don't let a
strong 1-hour track record create pressure to fast-track this one — the
whole reason it's tracked independently is that a shorter window with the
same fee curve is a genuinely different bet, not just a faster version of
the same one.
Done when: the 15-minute `unlock_real_orders` flag reflects its own
accumulated trade count and ROI, completely independent of the 1-hour
figure.

---

## Reference links gathered during planning

- Polymarket API docs: [https://docs.polymarket.com](https://docs.polymarket.com)
- Gamma API base: `https://gamma-api.polymarket.com`
- CLOB API base: `https://clob.polymarket.com` (duration-agnostic — works
  off token_id regardless of which duration it came from)
- Binance klines: `https://api.binance.com/api/v3/klines`
- Binance market-data mirror (untested, worth trying if the main domain is
  ever blocked again): `https://data-api.binance.vision`
- Chainlink feed explorer (verify address here): [https://data.chain.link/feeds/polygon/mainnet/btc-usd](https://data.chain.link/feeds/polygon/mainnet/btc-usd)
- Chainlink Data Streams (the actual, harder-to-access resolution source): [https://data.chain.link/streams/btc-usd](https://data.chain.link/streams/btc-usd)
- Chainlink docs — consuming Data Feeds: [https://docs.chain.link/data-feeds/using-data-feeds](https://docs.chain.link/data-feeds/using-data-feeds)
- Free Polygon RPC: `https://polygon-rpc.com` (fallback: `https://rpc.ankr.com/polygon`)
- Scheduler with free 1-minute intervals: [https://cron-job.org](https://cron-job.org)
- Confirmed 15-min event slug example: [https://polymarket.com/event/btc-updown-15m-1786175400](https://polymarket.com/event/btc-updown-15m-1786175400)

**Still needs your own search (see 1.4):** the Gamma API `series_slug`
value for the 15-minute BTC series. Query the Gamma events endpoint
directly with a few candidate values, or inspect Polymarket's own network
requests on the 15-minute markets page.
