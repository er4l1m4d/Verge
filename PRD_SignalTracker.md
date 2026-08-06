# PRD: Signal Tracker (working title)

**Status:** Draft v1
**Owner:** Oluwadamilare
**Naming:** Not yet decided — separate session, see MatchFun/Ciphra precedent

---

## 1. Summary

A personal decision-support tool for Polymarket's 1-hour BTC Up/Down market.
It does not predict price direction. It scores whether the market's current
odds disagree enough with a weighted set of technical indicators — after
accounting for Polymarket's trading fee — to be worth a bet, and enforces its
own risk discipline, including refusing to unlock real-money order prep until
the strategy has proven itself on paper.

The product is, in one sentence: **a bet filter with a memory**, not a
prediction engine.

---

## 2. Problem & Philosophy

1-hour crypto markets are close to zero-sum against professional, well-capitalized
traders. There is no achievable "guaranteed accuracy" — the honest goal is a
small, measurable, repeatable statistical edge, defended by strict risk rules,
validated before real money touches it.

Three non-negotiable principles carried through every part of this PRD:

- **Decision support, not prediction.** The tool never claims to know where
  BTC is going. It only ever answers: *is this specific bet, at this price,
  after this fee, worth taking* — or should it be skipped.
- **Risk management outranks accuracy.** A high win rate with poor
  risk/reward will still lose money; a modest win rate with disciplined
  sizing survives. Every signal is accompanied by position size, not just a
  direction.
- **Nothing gets trusted until it's proven.** Backtest before paper trading.
  Paper trade before real money. The system itself — not willpower — enforces
  that order.

---

## 3. User & Context

Single user (Oluwadamilare), personal use, $0 starting budget, free-tier
infrastructure only. Needs access from anywhere (phone included), so the tool
must be hosted, not run locally. Comfortable vibecoding — the PRD favors
concrete, opinionated defaults over open-ended options so implementation can
move fast without stalling on decisions.

---

## 4. Scope — v1

- **Asset:** BTC only.
- **Market:** Polymarket's 1-hour BTC Up/Down market only.
- **Capability:** live signal generation, backtesting, auto-tracked paper
  trading, Telegram alerts.
- **No wallet integration, no real orders, in v1.** Every "trade" in v1 is
  simulated and logged.

## 5. Out of Scope (v1)

- ETH / SOL support (planned v2, same architecture, parameterized by asset)
- 15-minute markets (deliberately deferred — see §16, fees are punishing
  there and the resolution window leaves less room for the "first 40 minutes"
  edge the strategy relies on)
- Semi-automated order prep / wallet connection (Phase 4, gated — see §12)
- Any UI beyond the single-screen dashboard described in VERGE.md
- Multi-user auth (this is a single-operator tool; basic access protection —
  e.g. a shared secret or simple login — is still required since it's
  publicly hosted, but no user-account system)

---

## 6. Core Mechanics

### 6.1 Resolution source & anchor

Polymarket's hourly BTC market resolves strictly against **Binance's
BTC/USDT pair**, comparing the open and close of the specific 1-hour candle
named in the market title — nothing else (not CoinGecko, not spot, not any
other exchange). All price data and indicator calculations must be sourced
from Binance's public klines endpoint, not CoinGecko, and every "current
price" reference in the system must be relative to **the active market's
hour-open**, fetched and locked at the moment that hour's market opens.

### 6.2 Indicators & weighting (locked in from prior sessions)

| Indicator | Weight | Signal condition |
|---|---|---|
| RSI (14-period) | 40% | RSI > 70 → −1 (overbought, bet LOWER); RSI < 30 → +1 (oversold, bet HIGHER); else 0 |
| Volume spike | 35% | Volume ≥ 3× 10-period average on one side → ±1 in that side's direction; else 0 |
| MA crossover (5-min fast / 15-min slow) | 25% | Fast > Slow → +1; Fast < Slow → −1; equal → 0 |

Indicator lookback windows are recalculated every cycle relative to the
current hour, so a 14-period RSI on 5-minute candles (70 min of history) is
explicitly documented as *intentionally* spanning slightly beyond the current
hour's window — this is a known, accepted tradeoff, not an oversight, and is
one of the first things the backtest should validate or challenge.

### 6.3 Decision thresholds

| Total weighted score | Decision | Confidence |
|---|---|---|
| ≥ 0.6 | BET HIGHER | High |
| 0.4 to 0.6 | BET HIGHER | Low |
| −0.4 to 0.4 | SKIP | — |
| −0.6 to −0.4 | BET LOWER | Low |
| ≤ −0.6 | BET LOWER | High |

### 6.4 Fee-adjusted edge calculation

Polymarket charges a taker fee on crypto markets of
`fee = shares × 0.07 × price × (1 − price)`, peaking near 50¢ and shrinking
toward the extremes. Maker (limit) orders pay zero fee and earn a rebate.

The system must:
- Compute the fee-adjusted expected value of a signal **before** it's
  surfaced as BET HIGHER/LOWER — a raw score that clears the threshold but
  whose edge is erased by the fee must resolve to SKIP, not a low-confidence
  bet.
- Default every suggested action to a **limit order price**, not a market
  order, since maker execution avoids the fee entirely. The dashboard shows
  the suggested limit price, not just "buy YES."
- Display the fee-adjusted edge explicitly (`--cost-amber` in VERGE.md) so
  it's never an invisible deduction.

### 6.5 Position sizing / risk rules

Hardcoded, not user-adjustable in v1 (avoids the temptation to override
discipline mid-session):

- Max 1–2% of tracked bankroll per bet (paper bankroll in v1; real bankroll
  once Phase 4 unlocks).
- Hard stop-loss alert at −15 to −20% of position value.
- Take-profit alert at +20 to +30% of position value.
- No bets suggested in the final 10 minutes of the hour (odds efficiency
  decay — the edge lives in the first ~40 minutes).

---

## 7. Data Sources & APIs

| Source | Use | Auth |
|---|---|---|
| Binance public REST (klines) | Price history, RSI/MA/volume calculation | None required |
| Polymarket Gamma API | Discover the live hourly BTC market, get `clobTokenIds` | None required |
| Polymarket CLOB API | Live odds, order book depth | None required (read-only) |
| Polymarket CLOB `prices-history` | Historical odds, for the mispricing backtest | None required |
| Telegram Bot API | Push alerts | Bot token only |

No API key or secret is ever exposed client-side. All external calls are
proxied through the Render backend.

---

## 8. System Architecture

- **Frontend:** Vercel — single-page dashboard per VERGE.md.
- **Backend:** Render (single free Web Service) — signal engine, REST API
  for the frontend, Telegram push logic.
- **Database:** Supabase (Postgres) — signal log, paper trade log, running
  win-rate/ROI, graduation-gate counters.
- **Scheduler:** UptimeRobot or cron-job.org, hitting one endpoint —
  `/api/heartbeat` — every 5 minutes. This single call does three jobs at
  once: keeps the Render free-tier service awake, runs the signal check for
  the current BTC hour window, and fires a Telegram alert if the decision
  clears threshold. No paid background worker required.
- **Telegram bot:** New, dedicated bot — separate from ARIA. Push-only in
  v1 (no need for a long-polling listener, since alerts originate from the
  heartbeat, not from inbound user messages); optional `/status` command via
  webhook can be added later without changing the hosting model.

---

## 9. Backtesting (build before paper trading)

Two distinct backtests, run in this order:

1. **Directional backtest** — using free historical Binance klines, test
   whether the RSI+Volume+MA weighted combination actually predicts the
   direction of BTC's hourly candle close vs. open, independent of Polymarket
   entirely. Cheap, fast, and the first real validation of whether the
   strategy has any signal at all.
2. **Mispricing backtest** — using Polymarket's `prices-history` for the
   hourly BTC market (as far back as history exists), test whether the
   indicator combination correctly identified moments where Polymarket's
   odds diverged from the eventual outcome. This is the actual thesis of the
   tool ("catch the crowd's overreactions") and is the backtest that matters
   most, but is bounded by however much historical odds data actually exists
   for this market.

**Done when:** both backtests run against at least 90 days of historical
data (or the maximum available, if shorter) and produce a report showing
win rate, average win/loss, and ROI, before any live paper trading begins.

---

## 10. Paper Trading & Persistence

Every signal generated by the live engine — BET HIGHER, BET LOWER, or SKIP —
is logged to Supabase automatically: timestamp, market window, indicator
values, score, decision, suggested price/size, and (once the hour resolves)
the actual outcome and simulated P&L. No manual spreadsheet.

The dashboard's history band and win-rate figure are read directly from this
table — there is no separate "tracking system," the log *is* the product's
memory.

**Done when:** a full hour cycle — signal generated, hour resolves, outcome
recorded, win rate updates — runs unattended, end to end, with no manual
step.

---

## 11. Notifications

Telegram push, via the dedicated bot, fires only when a signal clears the
BET HIGHER/LOWER threshold (not on every SKIP — that would be constant
noise for a market that resolves every hour). Message includes: direction,
confidence, suggested limit price and size, fee-adjusted edge, and minutes
remaining in the window.

---

## 12. Graduation Gate (enforced in code, not just discipline)

Real-order-prep functionality (Phase 4) is **locked behind a condition
checked by the system itself**, not a rule the user has to remember:

```
unlock_real_orders = (paper_trade_count >= 200) AND (cumulative_roi > 0)
```

Both values are read live from the Supabase paper-trade log. Until both are
true, any UI or API path toward real order construction is disabled outright
— not hidden behind a warning, genuinely inaccessible.

---

## 13. Future Phase: Semi-Auto Execution (post-gate)

Once unlocked: wallet connection (user's own wallet, signing client-side —
the tool never holds a private key), order construction at the suggested
limit price/size, user reviews and confirms, wallet signs and submits via
EIP-712. This phase is intentionally undesigned in detail here — it gets its
own PRD addendum once the gate is actually cleared, since building it now
would be solving a problem that may not exist yet if the backtest or paper
trading doesn't validate the edge.

---

## 14. Design

See `VERGE.md` — dark-only single-screen terminal, Geist Sans/Mono, six-token
color system (one job per hue), SKIP rendered with full conviction rather
than as a disabled state, probability bar showing the literal gap between
market odds and indicator-implied probability.

---

## 15. Success Metrics

- **Phase gate 1 (backtest):** positive expected ROI over ≥90 days of
  historical data, on both the directional and mispricing backtests.
- **Phase gate 2 (paper trading):** ≥200 logged paper trades with cumulative
  ROI > 0 — the literal graduation-gate condition.
- **Ongoing:** win rate and ROI are tracked, not accuracy alone — a
  profitable 45% win rate with good risk/reward is a success; an unprofitable
  70% win rate is a failure, by design.

---

## 16. Risks & Known Limitations

- **No guaranteed edge.** Backtests can overfit to historical conditions
  that don't repeat; this is explicitly why paper trading is a second,
  independent gate rather than a formality.
- **Fees now apply broadly to crypto markets** (not just 15-minute, as
  earlier research assumed), peaking near 50/50 odds — exactly where the
  SKIP threshold sits. This materially raises the bar for what counts as a
  real edge and is why fee-adjustment is in the core scoring path, not a
  later polish item.
- **Thin market supply.** Polymarket lists a small number of concurrent
  1-hour BTC markets at any time, which limits how much live signal volume
  is available for paper trading — one more reason the historical backtest
  matters, since it can use far more data than live paper trading can
  accumulate quickly.
- **Render free-tier sleep** is mitigated by the heartbeat-as-trigger
  pattern, but is still dependent on an external scheduler staying reliable;
  a missed heartbeat means a missed signal window, not a crashed system.
- **Whale/professional dominance** in short-duration markets is real and
  documented — this tool is not attempting to compete on speed, only on
  discipline and fee-aware pricing, which is a different game.

---

## 17. Open Questions

- Exact backtest lookback window once historical Polymarket odds data
  availability is confirmed (may be shorter than the 90-day target in §9).
- Whether the graduation gate's 200-trade/positive-ROI threshold should
  scale with confidence level (e.g., require more trades at low confidence)
  — parked for the build-plan discussion rather than decided here.
- Basic access protection mechanism for the publicly hosted dashboard
  (shared secret vs. simple login) — needs a decision before deployment,
  not before build.
