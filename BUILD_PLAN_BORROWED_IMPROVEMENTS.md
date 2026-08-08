# Build Plan: Borrowed Improvements (Addendum)

Companion to the existing Verge build plans. Everything here was selected
from a comparison against `aulekator/Polymarket-BTC-15-Minute-Trading-Bot`
for being genuinely useful *and* compatible with Verge's actual constraints
(free-tier, single-user, heartbeat/sleep architecture, graduation-gate
discipline). Deliberately excludes that repo's heavier infrastructure
(NautilusTrader, Redis, WebSocket streaming, Grafana/Prometheus) — see the
chat discussion for why each of those would work against, not for, this
project. 5 phases.

---

## Phase 1 — Odds-vs-Momentum Divergence Signal

**Goal:** a fourth, genuinely different signal — comparing Polymarket's own
odds against recent price momentum, something none of RSI/MA/Volume do.

**1.1 — Build the divergence calculation as a standalone, testable function**
How: A new function, e.g. `odds_momentum_divergence(odds, price_series)`,
implementing two sub-checks against data Verge already has on hand each
cycle (no new API calls needed):
  - **Extreme-odds fade**: if odds ≥ ~0.68 (or ≤ ~0.32) but recent spot
    momentum doesn't strongly confirm that direction, lean the opposite way
    — markets rarely sustain that much conviction right at window open.
  - **Momentum-not-yet-priced-in**: if odds sit near 50/50 (say 0.35-0.65)
    but recent momentum is meaningfully directional, lean with momentum.
Return a signed value in the same -1/0/+1 shape as `ma_crossover`/
`volume_spike`, so it can slot into the existing scoring function without
restructuring it. Start with the thresholds as constants you can tune
later (0.68/0.32 for extreme, a small momentum threshold like 0.3% over
the last ~15 minutes of data), not hardcoded magic numbers scattered
through the function body.
Done when: feeding it a few hand-constructed cases (odds pinned at 0.75
with flat momentum; odds at 0.50 with strong upward momentum; odds at 0.50
with no momentum) returns the expected direction each time.

**1.2 — Run it in shadow mode before it touches the real score**
How: This is the important discipline point, consistent with how every
other signal in this project earned its way in — don't blend it into the
weighted score immediately. Compute it every cycle alongside the existing
RSI/MA/Volume score, log it to the `signals` table as an extra field
(e.g. `divergence_signal`), but don't let it affect `final_decision` yet.
Done when: signals are accumulating with this new field populated, purely
as an observation, for at least a couple of weeks of live cycles.

**1.3 — Backtest it against history before assigning it a real weight**
How: Same discipline as the original RSI/MA/Volume backtest — run the
divergence calculation against historical price and (where available)
historical odds data, and check whether it would have been directionally
correct often enough to be worth including. Compare its standalone
accuracy, and — more importantly — whether adding it changes the combined
score's accuracy when blended in at a few candidate weights (e.g. what
happens to backtest ROI if RSI/MA/Volume/Divergence become
30/25/20/25 instead of 40/35/25/0).
Done when: you have a specific, backtested weight for this signal — not a
guess — or a documented decision that it doesn't earn a place in the score
and stays observational only.

**1.4 — Promote it into the live score, only after 1.3 clears**
How: Update the weighted scoring function to include the new signal at its
backtested weight, re-normalizing the others. Keep the shadow-mode logging
in place even after promotion — it's cheap, and it's your early warning if
this signal's usefulness drifts over time.
Done when: `final_decision` is influenced by the divergence signal, and a
manual spot-check of a few recent signals shows the new component
contributing sensibly to the score, not dominating or being ignored.

---

## Phase 2 — Free Sim/Live Mode Toggle

**Goal:** the "switch modes without redeploying" convenience the other
repo gets from Redis, using infrastructure you already pay nothing for.

**2.1 — Add a single settings row in Supabase**
How: A small `settings` table (or a single row in an existing config
table) with a `mode` column (`"paper"` / `"live"`), defaulting to
`"paper"`. This becomes the one flag every future real-order code path
(Phase 4-of-the-PRD, still gated behind the 200-trade/positive-ROI
graduation condition) checks before doing anything with real money.
Done when: reading and writing this flag from a local script works
against your real Supabase project.

**2.2 — Wire the flag into the heartbeat and signal endpoints**
How: At the top of the heartbeat cycle, read the current mode once and
pass it through — in paper mode, behavior is exactly what exists today
(simulated trades only). This task is deliberately a no-op today, since
Phase 4-of-the-PRD (real execution) doesn't exist yet — it's groundwork so
that when it does, flipping to live mode is a one-row database update
instead of a redeploy.
Done when: the flag is read once per heartbeat cycle and logged, even
though nothing yet branches on it besides the log line.

---

## Phase 3 — Resilient Ingestion

**Goal:** stop a single transient network blip from silently becoming a
SKIP with no distinction from "the signal genuinely wasn't there."

**3.1 — Add a retry-with-backoff wrapper for external calls**
How: A small helper, e.g. `fetch_with_retry(fn, retries=2, backoff=1.5)`,
wrapping the Binance/Gamma/CLOB/Chainlink calls that currently fail silently
into a SKIP on the first error. Two retries with short exponential backoff
(roughly 1-2 seconds total added delay) is enough to ride out a dropped
connection without meaningfully delaying the heartbeat cycle.
Done when: temporarily pointing one of these calls at a bad URL shows two
retry attempts in the logs before it finally gives up and falls through to
the existing SKIP behavior — i.e., the fallback path still works, it's
just no longer triggered by a single blip.

**3.2 — Distinguish "no signal" from "fetch failed" in the note field**
How: Right now, insufficient data and a fetch failure can both surface as
a generic SKIP note. Make the note explicit about which one happened
(`"Fetch failed after retries"` vs `"Insufficient price data"`), so a
future review of SKIP patterns can tell real thin-signal periods apart
from infrastructure hiccups.
Done when: forcing a fetch failure and forcing a genuine thin-data
condition produce visibly different note text in the logged signal.

---

## Phase 4 — Rolling-Window Performance Trend

**Goal:** catch a strategy quietly degrading before the all-time cumulative
number is stale enough to hide it.

**4.1 — Add a rolling-window stats query alongside the existing cumulative one**
How: Extend `get_stats()` (or add a sibling function) to also compute win
rate and ROI over just the last N resolved trades (e.g. last 30, or last
7 days) per duration, not just all-time cumulative. A strategy that was
strong for its first 150 trades and has been losing for the last 30 looks
identical to "profitable overall" in a cumulative number — the rolling
window is where you'd actually see the drift.
Done when: `/api/stats` returns both the existing cumulative figures and a
rolling-window figure, and they visibly diverge in a synthetic test where
recent trades are deliberately worse than older ones.

**4.2 — Surface it on the dashboard**
How: A small addition to the history band — the existing cumulative win
rate stays as the headline, with a smaller rolling-window figure next to
it. No new visual language needed, this fits the existing DESIGN.md
component, just a second number in the same spot.
Done when: the two figures are both visible and clearly labeled as
different windows, not easily confused with each other.

---

## Phase 5 — Optional: Fear & Greed as a Non-Directional Gate

**Goal:** the one idea worth borrowing cautiously, kept deliberately small
given the timeframe mismatch concern.

**5.1 — Pull the daily reading, use it only to adjust confidence, never direction**
How: The Fear & Greed Index (free, no key, via `alternative.me`'s public
API) updates roughly once a day — far too coarse to justify picking a
direction for a 15-minute market. The only defensible use: on days at the
extreme ends of the scale (very high fear or very high greed), slightly
raise the bar for what counts as a strong-enough signal to bet on, on the
theory that regime-level extremes correlate with noisier short-term price
action. It should never contribute a directional +1/-1 the way RSI/MA/
Volume/Divergence do.
Done when: on a day flagged as extreme, the minimum score threshold to
clear BET HIGHER/LOWER is measurably stricter than on a normal day, and
nothing about *which direction* gets bet is influenced by this reading.

**5.2 — Treat this phase as genuinely optional**
How: Given the timeframe mismatch, don't feel obligated to build this one.
If Phase 1's divergence signal and Phase 4's rolling-window visibility are
already giving you what you need to judge signal quality, this phase adds
the least value of everything here for the effort involved.
Done when: you've made a deliberate decision either way, not defaulted
into building it just because it was on the list.
