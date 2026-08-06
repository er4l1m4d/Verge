# Build Plan: Signal Tracker (working title)

Companion to `PRD_SignalTracker.md` and `VERGE.md`. 10 phases, ~45 tasks.
Each task states **how** to build it and a concrete **done when** condition.
Follow the phase order — later phases assume earlier ones are actually
working, not just written.

---

## Phase 0 — Project Scaffold & Environment

**Goal:** a repo that can hold everything below without restructuring later.

**0.1 — Repo layout** ✅
How: Single Git repo, two top-level folders: `backend/` (Python) and
`frontend/` (static/React). A third folder, `data/`, holds nothing in git
except a `.gitkeep` — it's where cached historical data lives locally during
backtesting and is gitignored.
Done when: repo pushed to GitHub with this structure and an empty
`README.md` in each folder.
Status: Structure complete, commit `ecf3734`. GitHub push deferred — requires
remote URL before `git remote add origin <url> && git push -u origin master`.

**0.2 — Backend environment** ✅
How: `backend/requirements.txt` with `flask`, `flask-cors`, `requests`,
`pandas`, `numpy`, `python-telegram-bot`, `supabase` (the Python client),
`python-dotenv`, `gunicorn`. Create a virtualenv, install, confirm
`python -c "import flask, pandas, supabase"` runs clean.
Done when: `pip install -r backend/requirements.txt` succeeds from a fresh
virtualenv with no errors.
Status: All 9 packages + deps installed into `backend/venv/`. Import check passes.

**0.3 — Supabase project**
How: Create a free Supabase project. Note the project URL and anon/service
keys. Don't design the schema yet — that's Phase 5, once you know exactly
what a signal record needs to contain.
Done when: you can connect from a local Python script using the `supabase`
client and successfully run `select 1`.

**0.4 — Environment variables**
How: `backend/.env` (gitignored) holding `SUPABASE_URL`, `SUPABASE_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Load via `python-dotenv` locally;
these get re-entered as real environment variables in Render's dashboard at
deploy time (Phase 8), never committed.
Done when: a local test script reads all four values via `os.environ` with
no hardcoded secrets anywhere in the codebase.

---

## Phase 1 — Historical Data Pipeline

**Goal:** the raw material both backtests in Phase 3 depend on.

**1.1 — Binance klines fetcher**
How: A function `get_binance_klines(symbol, interval, start_time, end_time)`
that calls Binance's public REST klines endpoint
(`GET https://api.binance.com/api/v3/klines`) with `symbol=BTCUSDT`,
paginating through `limit=1000`-row chunks since Binance caps each response.
No API key needed. Return a pandas DataFrame with open/high/low/close/volume
and timestamp, for both `1h` and `5m` intervals — you need both, since the
market resolves on 1h candles but the indicators run on 5m data.
Done when: calling the function for a 90-day range returns a complete,
gap-free DataFrame you can plot and visually sanity-check against a known
price chart.

**1.2 — Local data cache**
How: Save fetched klines to `data/btc_1h.parquet` and `data/btc_5m.parquet`.
On each run, check what's already cached and only fetch the missing tail —
don't re-download 90 days every time you tweak a threshold.
Done when: a second run of the fetcher completes in under a second because
it's reading from cache instead of hitting Binance again.

**1.3 — Polymarket historical odds fetcher**
How: A function that, given a known market's CLOB token ID, calls the
`prices-history` endpoint on the CLOB API to pull historical price (odds)
points for that token. Since each hourly market is a *separate* market with
its own token IDs (unlike a continuous asset), you'll first need to
enumerate past hourly BTC markets via the Gamma API's events/markets search
(filter by slug pattern, e.g. markets whose slug starts with
`bitcoin-up-or-down`) to get a list of token IDs to query. Exact query
parameters should be checked against Polymarket's current API docs at
build time — this endpoint has changed shape before.
Done when: you have historical odds data for at least a handful of past
hourly BTC markets, timestamped, ready to align against Binance data.

**1.4 — Data alignment**
How: For each historical hourly market, join its Polymarket odds timeline
with the Binance 1h candle covering the same hour, and the preceding 5m
candles for indicator calculation. Store the merged result as one row per
historical market: hour-open price, hour-close price, actual direction,
Polymarket odds at several points during the hour, and the raw 5m price
series needed to recompute indicators for that hour.
Done when: you have a single clean table where each row is one historical
hourly market with everything needed to backtest it, with no missing joins.

---

## Phase 2 — Indicator & Scoring Engine

**Goal:** pure functions, no network calls, fully unit-testable — this is
the logic both the backtest and the live engine will both call.

**2.1 — RSI function**
How: `calculate_rsi(prices, period=14)` — standard Wilder's RSI or simple
average-gain/average-loss version (either is fine; document which one you
used, since it affects backtest results if you change it later). Takes a
price series, returns a single current RSI value.
Done when: tested against a known reference RSI value (e.g. cross-check one
output against a chart on TradingView for the same timestamp) within a
reasonable rounding tolerance.

**2.2 — MA crossover function**
How: `ma_crossover(prices, fast=5, slow=15)` — compute both moving averages
over the given windows (in 5-minute bars, so `fast=5` bars ≈ 25 minutes;
confirm this matches the "5-min / 15-min" framing from the PRD, adjusting
bar-count vs. minute-count as needed), return `+1`, `-1`, or `0`.
Done when: returns correct sign against a few hand-picked synthetic price
series where the trend direction is obvious by eye.

**2.3 — Volume spike function**
How: `volume_spike(current_volume, avg_volume, direction)` — ratio-based,
returns `+1`/`-1` if ratio ≥ 3.0 in that direction, `0` otherwise, per the
thresholds already locked in the PRD.
Done when: unit tests cover the ≥3x, between 2x-3x, and <2x cases correctly.

**2.4 — Weighted scoring function**
How: `score_signal(rsi_val, ma_val, volume_val) -> (score, decision, confidence)`
combining the three per the PRD's weights (40/25/35) and thresholds
(±0.6 / ±0.4). This function is the single source of truth for the decision
— both backtest and live engine call it, so there is never a second copy of
this logic to drift out of sync.
Done when: feeding it the worked example from the PRD (RSI overbought,
volume spike higher, MA uptrend → total +0.20 → SKIP) reproduces that exact
result.

**2.5 — Fee-adjusted edge function**
How: `fee_adjusted_edge(decision, odds_price, shares)` implementing
`fee = shares * 0.07 * price * (1 - price)`, then comparing the potential
payout minus fee against the stake to produce an edge percentage. If the
fee erodes a borderline signal below a minimum viable edge (define this as
a constant, e.g. 3%), downgrade the decision to SKIP even if the raw score
cleared threshold.
Done when: a signal with a raw score just above 0.4 but near 50¢ odds (worst
fee case) correctly downgrades to SKIP, while the same score at 80¢ odds
(cheap fee zone) does not.

**2.6 — Unit test suite**
How: `pytest` covering all five functions above with the hand-checked cases
already described, plus a couple of edge cases (empty price series, zero
average volume, RSI on fewer than 14 data points).
Done when: `pytest backend/tests/` passes with no failures and no skipped
tests.

---

## Phase 3 — Backtesting Engine

**Goal:** know whether the strategy has any real edge before it ever runs
live. This phase is why Phase 1 and Phase 2 exist.

**3.1 — Directional backtest runner**
How: Loop through every historical hour in the Phase 1 dataset, recompute
RSI/MA/volume from the 5m data available *as of that hour's open* (careful
not to leak future data), run it through the Phase 2 scoring function, and
compare the resulting decision against what the Binance candle actually
did. Output a row per hour: decision, actual outcome, correct/incorrect.
Done when: running this over the full cached dataset produces a results
table with a computed win rate and a count of how many hours were skipped
vs. acted on.

**3.2 — Mispricing backtest runner**
How: Same loop, but this time also pull in the Polymarket odds recorded at
signal-generation time for that historical hour (from Phase 1.3/1.4), and
score against whether the indicator combination correctly identified a
divergence between odds and eventual outcome — this is the real test of the
tool's thesis, not just "can RSI predict BTC."
Done when: produces a results table specifically flagging the hours where
the signal disagreed with market odds, with win rate calculated only on
those disagreement hours.

**3.3 — Backtest report generator**
How: A script that takes the results tables from 3.1 and 3.2 and outputs a
readable summary — win rate, average win size, average loss size, simple
ROI assuming the PRD's 1-2% position sizing rule, and a plot of cumulative
P&L over the backtest period (matplotlib is fine, this never needs to be
fancy).
Done when: running the report script produces a single readable output
(printed summary + saved PNG chart) that answers "did this strategy make
money over the backtest period" without needing to read raw data.

**3.4 — Threshold sensitivity check** *(optional, do only if 3.3's result
is borderline rather than clearly positive or clearly negative)*
How: Re-run 3.1/3.2 with the RSI threshold shifted (e.g. 75/25 instead of
70/30) and the indicator weights adjusted, to see whether the strategy is
robust or was only ever profitable at one specific, possibly overfit,
setting.
Done when: you have enough evidence to say with some confidence whether the
edge is real or an artifact of the exact thresholds chosen.

**Phase 3 gate:** Do not proceed to Phase 4 until 3.3 shows a plausible
positive edge. If it doesn't, that's a valid and useful outcome — it means
going back to indicator selection before writing a single line of live
infrastructure.

---

## Phase 4 — Live Signal Engine & Backend API

**Goal:** the same scoring logic from Phase 2, now running against live
data instead of historical data.

**4.1 — Market discovery**
How: `get_current_hourly_market()` — query the Gamma API for the currently
open hourly BTC market (matching on slug pattern and an active/unresolved
status), extract its `clobTokenIds` and the hour's official open time.
Cache this per hour so you're not re-querying market discovery every 5
minutes within the same hour.
Done when: calling this function at any point during a live hour returns
the correct current market and its token IDs, verified by manually
comparing against what's showing on polymarket.com.

**4.2 — Live odds fetcher**
How: `get_current_odds(token_id)` — query the CLOB API for the current
best price on the relevant token, returning it as an implied probability
(0-1).
Done when: the returned value matches what's displayed live on Polymarket's
market page within a few seconds of latency.

**4.3 — Live price fetcher**
How: `get_current_price_data()` — pull the most recent Binance 5m candles
(enough for RSI-14 and both MAs), plus explicitly fetch and store the
current hour's open price the moment a new hour starts (needed for
Phase 6's resolution checker later).
Done when: returns a DataFrame usable directly by the Phase 2 indicator
functions with no reshaping needed.

**4.4 — Signal endpoint**
How: Flask route `GET /api/signal` that chains 4.1 → 4.2 → 4.3 → the
Phase 2 scoring function → Phase 2.5's fee adjustment, and returns one JSON
object: decision, confidence, score, each indicator's value and
contribution, current odds, fee-adjusted edge, suggested limit price, and
minutes remaining in the hour.
Done when: hitting this endpoint locally returns a complete, correctly
shaped JSON response using real live data, not mocks.

**4.5 — Health endpoint**
How: `GET /api/health` returning status + timestamp, trivial but needed for
Phase 6's heartbeat and for confirming the Render deploy is alive.
Done when: returns 200 with a timestamp that updates on each call.

---

## Phase 5 — Persistence Layer

**Goal:** the log that turns "a tool that runs" into "a tool with a track
record."

**5.1 — Schema design**
How: Two Supabase tables. `signals`: id, timestamp, market_window_start,
decision, confidence, score, rsi/ma/volume raw values, odds, fee_adjusted_edge,
suggested_price, suggested_size. `paper_trades`: references a signal id,
adds resolved_outcome (nullable until the hour closes), simulated_pnl,
resolved_at. Keep `signals` as the append-only log of every decision, and
`paper_trades` as the subset that were actually BET HIGHER/LOWER (SKIPs
don't need a trade record, just a signal record).
Done when: both tables exist in Supabase with correct types and you can
manually insert and query a test row from each.

**5.2 — Write-signal logic**
How: Every call to `/api/signal` (or more precisely, every heartbeat-driven
call — see Phase 6) also inserts a row into `signals`, and if the decision
isn't SKIP, a corresponding row into `paper_trades`.
Done when: after a few live signal generations, both tables show matching
rows with no missing writes.

**5.3 — Resolution checker**
How: A function, called by the heartbeat once a market's hour has closed,
that fetches the actual Binance close price for that hour, determines the
real outcome, and updates any `paper_trades` row for that window with
`resolved_outcome` and `simulated_pnl` (using the PRD's 1-2% position
sizing to compute a realistic simulated dollar result).
Done when: an hour that's fully resolved shows a non-null outcome and P&L
in the database without any manual update.

**5.4 — Stats endpoint**
How: `GET /api/stats` — aggregate query over `paper_trades`: total count,
win rate, cumulative simulated ROI. This is what both the dashboard's
history band and the Phase 9 graduation gate read from — one source, two
consumers.
Done when: returns correct aggregate numbers that match a manual count of
the underlying rows.

---

## Phase 6 — Heartbeat, Scheduling & Telegram

**Goal:** the tool runs itself without you sitting in front of it.

**6.1 — Heartbeat endpoint**
How: `GET /api/heartbeat` — calls the Phase 4 signal chain, the Phase 5
write logic, and the Phase 5.3 resolution checker (for the *previous* hour,
if it just closed) all in one request. Make it idempotent: if called twice
in quick succession within the same hour, it shouldn't create duplicate
signal rows — check whether a signal already exists for the current window
before inserting.
Done when: calling this endpoint repeatedly in a short window produces
exactly one signal record per market hour, not duplicates.

**6.2 — Telegram bot setup**
How: Create a new bot via BotFather (separate from ARIA), get its token,
message yourself once manually to get your chat ID. A small
`send_telegram_alert(message)` function wrapping a plain HTTPS POST to the
Telegram Bot API's `sendMessage` — no need for `python-telegram-bot`'s
polling machinery at all in v1, just the raw send call.
Done when: calling the function sends a real message to your Telegram.

**6.3 — Alert logic**
How: Inside the heartbeat, only call `send_telegram_alert` if the decision
is BET HIGHER or BET LOWER — never on SKIP, per the PRD (constant SKIP
pings would just be noise on an hourly-resolving market). Message includes
direction, confidence, suggested price/size, fee-adjusted edge, and minutes
remaining.
Done when: a live BET signal produces exactly one Telegram message with all
the right fields; a SKIP produces none.

**6.4 — External scheduler**
How: Once deployed (Phase 8), point UptimeRobot or cron-job.org at the live
`/api/heartbeat` URL, interval 5 minutes.
Done when: the log shows heartbeat hits arriving every 5 minutes without
manual triggering, and Render's service stays awake continuously during
active hours.

---

## Phase 7 — Frontend Dashboard

**Goal:** VERGE.md instrument surface, built.

**7.1 — Scaffold**
How: A single static page (`frontend/index.html` + vanilla JS, or a minimal
Vite + React setup if you'd rather — either is fine given how small this UI
is) that polls `GET /api/signal` and `GET /api/stats` on load and on an
interval (e.g. every 30 seconds, separate from the 5-minute backend
heartbeat — the frontend polling more often just keeps the countdown and
odds looking live).
Done when: the page loads and successfully renders raw JSON data from both
endpoints, unstyled.

**7.2 — Status band**
How: Asset/duration label, a live countdown computed client-side from
`minutes_remaining` (update every second locally rather than re-polling the
backend every second), and a heartbeat dot that reflects the timestamp of
the last successful `/api/heartbeat`-driven signal (pulled from `signals`
via a small addition to `/api/stats` or a dedicated field).
Done when: countdown ticks smoothly and the dot visibly changes state if
the last heartbeat is more than ~10 minutes old.

**7.3 — Decision band**
How: Render the decision label at full color per VERGE.md's token system —
this is the component where the SKIP-gets-full-conviction principle has to
actually show up in code, not just get described. Don't conditionally apply
lower opacity or grayscale to the SKIP state; give it its own full-saturation
color (`--idle-slate`) and equivalent type weight to the other two states.
Done when: a visual side-by-side of all three states shows genuinely equal
visual weight, not a "real" bet look next to a faded placeholder.

**7.4 — Evidence band**
How: Three stat blocks (RSI/Volume/MA) each showing raw value, sign,
weighted contribution, matching the wireframe in VERGE.md.
Done when: values update correctly on each poll and visually match the data
coming back from `/api/signal`.

**7.5 — Probability band**
How: A horizontal bar showing market odds, with a separate marker for the
indicator-implied probability (roughly: 50% ± the score's magnitude, or a
simpler derived value — decide the exact mapping when you build this, it's
a display concern, not a scoring concern), fee-adjusted edge number in
`--cost-amber`, and the suggested limit price/size text.
Done when: the visual gap between the two markers changes correctly as the
underlying score changes across different signals.

**7.6 — History band**
How: Pull the last 10 records from an extended `/api/stats` response
(recent trades with outcome), render as a compact strip of colored marks
plus the running win rate.
Done when: a completed paper trade appears in this strip on the next poll
after it resolves.

**7.7 — Styling**
How: Implement VERGE.md's tokens as CSS custom properties, load Geist
Sans/Mono (free, via `@fontsource` or self-hosted), tabular-nums on every
numeric element.
Done when: the page visually matches VERGE.md's instrument surface intent — dark canvas,
correct color usage per token, numbers aligned in monospace.

**7.8 — Mobile check**
How: Test at a narrow viewport (this is meant to be checked from a phone
per the original requirement). Bands should stack cleanly rather than
requiring horizontal scroll.
Done when: every band is legible and usable on a ~375px-wide viewport.

---

## Phase 8 — Deployment

**Goal:** accessible from anywhere, per the original requirement that
started this whole project.

**8.1 — Backend to Render**
How: New Web Service on Render, connect the GitHub repo, root directory
`backend`, build command `pip install -r requirements.txt`, start command
`gunicorn app:app`. Add all four environment variables from Phase 0.4 in
Render's dashboard.
Done when: `https://<your-service>.onrender.com/api/health` returns 200
from a fresh deploy.

**8.2 — Frontend to Vercel**
How: New Vercel project, root directory `frontend`, add an environment
variable pointing at the Render backend URL, referenced in the frontend
code instead of a hardcoded `localhost` URL.
Done when: the deployed Vercel URL loads and successfully pulls live data
from the deployed Render backend.

**8.3 — CORS**
How: On the Flask backend, restrict `flask-cors`'s allowed origins to the
specific Vercel URL via an environment variable, not a wildcard.
Done when: requests from the Vercel frontend succeed, and a quick manual
test from a different origin (e.g. curl with a fake `Origin` header) is
rejected.

**8.4 — Access protection**
How: Resolve the PRD's open question here — simplest viable option is a
single shared secret checked via a query param or header on every backend
route, entered once and stored in `localStorage` client-side (not
`sessionStorage`, so you don't have to re-enter it each visit — it's a
personal single-user tool, this is an acceptable tradeoff). No need for a
full login system for a one-person tool.
Done when: hitting any `/api/*` route without the correct secret returns
401, and the deployed frontend works normally because it's storing and
sending the secret automatically.

**8.5 — End-to-end smoke test**
How: With everything deployed, watch one full live hour cycle from the
actual production URLs — signal generated, logged, Telegram alert fires if
applicable, hour resolves, stats update.
Done when: you've personally watched this happen once, live, from your
phone, exactly as the original goal described.

---

## Phase 9 — Paper Trading Validation

**Goal:** accumulate the evidence the graduation gate needs.

**9.1 — Unattended monitoring window**
How: Let the heartbeat run for the first several days without intervening.
Manually spot-check a handful of signals against what Polymarket and
Binance actually showed at that time, to catch any subtle bugs the unit
tests didn't cover (timezone handling around the hour boundary is the most
likely culprit).
Done when: at least 20-30 live signals have been generated and manually
verified as accurate against real market data.

**9.2 — Graduation gate implementation**
How: Add a computed field to `/api/stats` — `unlock_real_orders: bool` —
implementing exactly the PRD §12 formula
(`paper_trade_count >= 200 AND cumulative_roi > 0`), read live from
Supabase on every call. This is the one flag any future Phase 4-of-the-PRD
work is required to check before exposing any real-order code path.
Done when: the flag correctly flips based on real data, verified by
checking it against a manual count/ROI calculation from the database.

**9.3 — Weekly review habit**
How: Not code — a lightweight personal checklist: review win rate trend,
ROI trend, and any signals that look like the indicators misfired, once a
week while paper trading accumulates. This is where you'd catch the
strategy quietly degrading before real money is ever on the line.
Done when: this is genuinely a habit you're doing, not a step you wrote
down and skipped.

---

## What's deliberately not in this plan

ETH/SOL support, 15-minute markets, and Phase 4-of-the-PRD (semi-auto real
execution) are all out of scope here by design — see PRD §5 and §13. Adding
them before this plan's Phase 9 gate clears would be building on top of an
unvalidated foundation.
