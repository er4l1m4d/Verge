# Build Plan: Universal Window Outcomes & Capture-Point Comparison (Addendum)

Companion to the Window Observation Logging plan. Closes the sampling
bias that plan would otherwise carry silently, then uses the now-unbiased
data to test whether a smarter single capture point beats today's
"whichever tick happened to run first" default. 2 phases — the second
can't produce a trustworthy answer until the first has been running long
enough to accumulate real data.

---

## Phase 1 — Record the True Outcome for Every Window, Not Just Bet-On Ones

**Goal:** close the blind spot where a SKIP'd window currently has no
recorded ground truth anywhere, even if window-observations logged a
strong signal deep into it.

**1.1 — Add a table for universal outcomes**
How:
```sql
CREATE TABLE IF NOT EXISTS window_outcomes (
    market_duration TEXT NOT NULL,
    market_window_start BIGINT NOT NULL,
    actual_outcome TEXT NOT NULL,  -- 'UP' or 'DOWN'
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (market_duration, market_window_start)
);
```
Deliberately not tied to `signals` or `paper_trades` via foreign key —
this records what actually happened in the market, independent of what
Verge decided to do about it.
Done when: the table exists and accepts a manual insert/select correctly.

**1.2 — Find windows needing resolution**
How: A window "needs resolution" once its close time has passed and it
doesn't yet have a `window_outcomes` row. Source the candidate list from
distinct `market_window_start` values in `window_observations` (since any
window worth resolving has at least one observation logged) that are
missing from `window_outcomes`:
```python
def get_unresolved_window_outcomes(client, duration: str) -> list[int]:
    obs_windows = client.table("window_observations") \
        .select("market_window_start") \
        .eq("market_duration", duration).execute()
    resolved_windows = client.table("window_outcomes") \
        .select("market_window_start") \
        .eq("market_duration", duration).execute()
    obs_set = {r["market_window_start"] for r in obs_windows.data}
    resolved_set = {r["market_window_start"] for r in resolved_windows.data}
    return sorted(obs_set - resolved_set)
```
Done when: this correctly returns only windows with observations but no
outcome yet, verified against a few known cases.

**1.3 — Extend resolution to write once, use for both**
How: This is the important part — don't compute the outcome twice.
`resolve_previous_window()` already determines the true UP/DOWN outcome
per window (via `_resolve_via_binance`/`_resolve_via_chainlink_ticks`) in
order to resolve `paper_trades`. Restructure so outcome determination
happens once per window and feeds *both* writes:
```python
for window_start in get_unresolved_window_outcomes(client, duration):
    window_close = window_start + config.duration_ms
    if window_close > int(time.time() * 1000):
        continue  # not actually closed yet

    outcome = (_resolve_via_binance(window_start, window_close)
               if config.price_resolution_source == "binance"
               else _resolve_via_chainlink_ticks(window_start, window_close))
    if outcome is None:
        continue  # not enough data yet, retry next heartbeat

    client.table("window_outcomes").insert({
        "market_duration": duration,
        "market_window_start": window_start,
        "actual_outcome": outcome,
    }).execute()

    # If a paper trade exists for this window, resolve it using this
    # same outcome — no second lookup, no risk of the two drifting
    # apart from independently-computed results.
    trade = get_paper_trade_for_window(client, duration, window_start)
    if trade and trade.get("resolved_outcome") is None:
        resolve_trade_with_outcome(client, trade, outcome)
```
Done when: a window with a paper trade gets both `window_outcomes` and
`paper_trades.resolved_outcome` populated from a single resolution pass;
a window with only observations (no bet) gets `window_outcomes` populated
and nothing else, no error.

**1.4 — Confirm this closes the actual gap**
How: After a few days running, spot-check: pick a window where the
official decision was SKIP but `window_observations` shows a real score
swing later in the window. Confirm `window_outcomes` now has a real
UP/DOWN answer for it — something that was completely unrecorded before
this phase.
Done when: you can point to at least one such window and see its true
outcome sitting there, ready to be compared against what the mid-window
observations showed.

---

## Phase 2 — The Capture-Point Comparison (Wait for Real Data First)

**Goal:** a fair test of whether a smarter single capture point beats
today's first-tick default — run as a standalone analysis script, not
live infrastructure, same pattern as the existing backtest tooling.

**Don't run this meaningfully until there's real volume behind it** — a
rough floor worth setting for yourself: enough resolved windows with a
full observation timeline that the comparison isn't just noise. A few
hundred windows is a reasonable bar; days of data, not hours.

**2.1 — Assemble the dataset**
How: For every window present in both `window_observations` and
`window_outcomes`, pull its full tick list (seconds_into_window, score,
decision, odds) alongside its true outcome. This is the complete,
unbiased dataset Phase 1 exists to produce — every window that had
observations, not just the ones that happened to get bet on.
Done when: you have one record per window, each containing its full
tick timeline plus ground truth, with no missing outcomes for any window
that has observations.

**2.2 — Define candidate capture-point rules as pure functions**
How: Each rule takes one window's tick list and returns a single decision
— this mirrors how `score_signal` is a single, shared source of truth
rather than duplicated logic per caller:
```python
def rule_first_tick(ticks): ...        # today's actual default — the baseline to beat
def rule_fixed_checkpoint(ticks, seconds): ...   # decision at a specific fixed point, several values worth trying
def rule_strongest_score(ticks): ...   # whichever tick had the largest |score| anywhere in the window
def rule_late_checkpoint(ticks, min_gap, max_price): ...  # Late Entry V3's own logic, reusing its guardrails
```
Done when: each rule runs cleanly against the Phase 2.1 dataset and
returns exactly one decision per window.

**2.3 — Score every rule against the same data, fee-adjusted**
How: For each rule, for each window, compare its returned decision
against `window_outcomes.actual_outcome`, applying the same fee-adjusted
edge math already used everywhere else in Verge — not raw win rate alone.
Output win rate, ROI, and trade count per rule, side by side.
Done when: you have one clear table — one row per rule, comparable
columns — with `rule_first_tick`'s row as the reference line every other
rule is measured against.

**2.4 — Report, reusing existing hygiene**
How: A simple report script, same pattern as the existing backtest
report (plot with `plt.close()` after saving, no lingering figures).
Done when: running it produces one readable output answering, plainly,
whether anything beat the first-tick baseline and by how much.

**2.5 — Promote only if a rule genuinely, clearly wins**
How: Same discipline as divergence, Fear & Greed, and ATR before it —
nothing changes in the live engine just because a rule looked good in
this report. If a candidate clearly and consistently outperforms
`rule_first_tick`, that's the point to actually change what triggers the
official decision in `_generate_signal_inner` — replacing the current
"whichever tick happened to run first" behavior with the winning rule,
deliberately. If nothing clearly beats it, that's a complete, useful
answer too: it means the current default, accidental as its origin was,
turned out fine — and now you'll actually know that, instead of assuming it.
