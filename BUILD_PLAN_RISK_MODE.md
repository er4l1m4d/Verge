# Build Plan: Risk Mode vs. Safe Mode Comparison (Addendum)

Companion to the existing Verge build plans. Adds a second, fully isolated
trading track — "risk mode," which bets every window regardless of signal
strength — run alongside the existing filtered "safe mode," purely to
measure whether the filter is actually adding value. Risk mode's numbers
must never be able to touch the real graduation gate. 6 phases.

---

## Phase 1 — Mode as a First-Class Dimension

**Goal:** extend the isolation pattern that already separates 1h/15m to
also separate safe/risk, so nothing downstream has to guess which track a
row belongs to.

**1.1 — Add a `mode` column, same discipline as `market_duration`**
How: `ALTER TABLE signals ADD COLUMN mode TEXT NOT NULL DEFAULT 'safe';`
and the same for `paper_trades`. Every existing row backfills as `'safe'`,
correctly — they all were.
Done when: existing rows all show `mode='safe'` with no nulls, same
verification you already did for `market_duration`.

**1.2 — Thread mode through config, not just duration**
How: Your signal generation function already accepts `duration`. Add
`mode` as a second parameter (`"safe"` default). Nothing about
market discovery, price fetching, or indicator calculation changes based
on mode — every window still gets scored exactly the same way. Mode only
changes what happens *after* the score is computed (Phase 2).
Done when: calling signal generation with `mode="safe"` produces byte-for-byte
identical behavior to today, and `mode="risk"` runs the same
scoring pipeline without altering it.

---

## Phase 2 — Risk Mode's Decision Logic

**Goal:** a forced bet on every window, in the direction the score
actually leans — not a coin flip, and not a copy of safe mode's SKIP.

**2.1 — Force direction from the score's sign, regardless of magnitude**
How: After the existing weighted score is computed, safe mode applies the
usual ±0.4/±0.6 thresholds. Risk mode instead does:

```python
if mode == "risk":
    if score > 0:
        final_decision = "BET HIGHER"
    elif score < 0:
        final_decision = "BET LOWER"
    else:
        final_decision = "BET HIGHER"  # explicit tiebreak, see 2.2
    confidence = "forced"  # distinct from safe mode's low/high, so it's never confused with a real conviction level
```
This still uses the *real* score's direction — it's not random. The only
thing removed is the magnitude requirement.
Done when: a window that would SKIP in safe mode (score = 0.15, say)
produces a real BET HIGHER or BET LOWER in risk mode, in the direction
that score actually leaned.

**2.2 — Decide the exact-zero tiebreak deliberately, not accidentally**
How: A perfectly balanced 0.0 score (all three indicators canceling out)
has no directional information at all — forcing it toward HIGHER by
convention (2.1's fallback) is fine, just make sure it's a documented,
consistent rule rather than whatever the code happens to fall through to.
Done when: you can state the tiebreak rule in one sentence and point to
exactly where it's implemented.

**2.3 — Keep fee-adjusted edge calculation, but let it inform, not gate**
How: Still compute and log the fee-adjusted edge for risk mode's forced
bets — you want to see it, since it'll usually be negative or thin (most
forced bets land near 50/50 odds, the worst spot on the fee curve). Just
don't let it downgrade the decision to SKIP the way it does in safe mode —
risk mode has no SKIP state at all, by design.
Done when: risk mode's logged trades show real (often unfavorable)
fee-adjusted edge numbers, and none of them are SKIP.

**2.4 — Use the same fixed position sizing as safe mode, not scaled by confidence**
How: Risk mode's `confidence` is always `"forced"` — don't let it map to a
smaller position size or anything that quietly gives it an advantage or
disadvantage against safe mode's sizing. Same 1-2% bankroll rule as
everywhere else in the PRD. Keeping this identical is what makes the
comparison in Phase 5 actually mean something.
Done when: a risk-mode trade and a safe-mode trade at the same odds use
identical position-sizing math.

---

## Phase 3 — Hard Isolation from the Graduation Gate

**Goal:** risk mode's trade count and ROI must be structurally incapable
of unlocking real-money order prep — not by convention, by code.

**3.1 — Scope every graduation-gate query to `mode='safe'` explicitly**
How: Find every place `unlock_real_orders` (or the underlying
count/ROI query) is computed and hardcode the `mode` filter to `"safe"` —
not a parameter that could accidentally be passed as `"risk"` from
somewhere else, a literal constant at the one place this gate is
evaluated.
Done when: attempting to call the gate-check function with
`mode="risk"` either raises an error or is simply not a code path that
exists — there should be no way to ask "has risk mode graduated," because
that question shouldn't be answerable by this system at all.

**3.2 — Add a test that asserts this explicitly**
How: A unit test that seeds 200+ fake risk-mode trades with strongly
positive simulated P&L, then asserts `unlock_real_orders` for the real
gate is still `False` (assuming safe mode's own count is below 200,
independent of what risk mode is doing). This is the one test in the
whole project I'd consider close to mandatory — it's guarding against the
exact failure mode where "well it's got 200 trades, why not use it"
quietly creeps in later.
Done when: this test exists, passes, and would fail loudly if the
isolation from 3.1 were ever accidentally removed.

---

## Phase 4 — Running Both Tracks

**Goal:** every heartbeat generates both a safe-mode and a risk-mode
outcome for the same window, without doubling your API calls.

**4.1 — Score once, branch twice**
How: Market discovery, price fetch, and indicator scoring are identical
between modes (Phase 1.2) — do that work exactly once per window, then
apply the safe-mode threshold logic and the risk-mode forced logic to the
*same* computed score, producing two separate signal/trade records from
one set of API calls. Don't re-fetch odds or price data per mode.
Done when: a single heartbeat cycle for one window produces exactly one
`signals` row with `mode='safe'` and one with `mode='risk'`, sharing
identical RSI/MA/Volume/odds values, differing only in decision and
confidence.

**4.2 — Suppress Telegram alerts for risk mode**
How: Risk mode fires a real bet on every single window — at 15-minute
cadence that's up to 96 alerts a day if left unfiltered, which would bury
the safe-mode alerts you actually want to act on. Don't send risk-mode
trades to Telegram at all; they're a background experiment, not something
to react to in the moment. If you want visibility, a daily digest summary
(Phase 5) is a better fit than per-trade pings.
Done when: risk mode accumulates trades silently, with zero Telegram
noise, while safe mode's alerts continue exactly as before.

---

## Phase 5 — The Comparison Itself

**Goal:** an honest comparison, not just two numbers sitting next to each
other — accounting for the calendar-time mismatch flagged earlier.

**5.1 — Basic side-by-side stats**
How: Extend `get_stats()` to accept `mode` alongside `market_duration`,
so you can pull safe-mode and risk-mode win rate/ROI independently, the
same pattern already used for duration. This gets you the raw comparison
right away.
Done when: `/api/stats?mode=safe` and `/api/stats?mode=risk` return
independently correct numbers.

**5.2 — Control for the calendar-time confound**
How: Since risk mode will hit 200 trades well before safe mode does, also
compute risk mode's win rate/ROI restricted to *only the calendar window
safe mode's 200 trades actually spanned* — i.e., once safe mode's 200th
trade resolves, note its first trade's timestamp, and separately report
what risk mode did between those same two timestamps, not just its own
first-200. This gives you two comparisons: "200 vs 200 trades" (fast, but
confounded by different time periods) and "same time period" (slower to
have enough safe-mode data for, but a fairer test).
Done when: the stats response can report both versions, clearly labeled,
rather than presenting the faster-but-confounded number as the whole
answer.

**5.3 — A weekly digest, not a live dashboard obsession**
How: A simple scheduled summary (could piggyback on the existing
heartbeat, gated to run once a week) posting both tracks' current
standing to Telegram as a single digest message — this is the
low-noise way to stay informed without per-trade alerts (4.2) or needing
to manually check the dashboard.
Done when: one message a week, not zero and not ninety-six.

---

## Phase 6 — Reading the Result Honestly

**Goal:** not code — a decision framework for once the numbers exist.

**6.1 — If safe mode clearly beats risk mode (accounting for 5.2)**
That's evidence the filter is adding real value beyond noise — reasonable
grounds to proceed toward the graduation gate on schedule.

**6.2 — If the two are close, or risk mode wins**
Treat this as a prompt to revisit the backtest — different indicator
weights, different thresholds, possibly different indicators entirely —
not as grounds to lower the bar for real money. A filter that isn't
proven yet is a reason to improve the filter, not remove it.

**6.3 — Don't let risk mode's faster trade count create pressure**
Restate the obvious on purpose: risk mode reaching 200 trades first is
expected and says nothing about which mode is better. The comparison in
5.2 is what matters, not which counter fills up first.
