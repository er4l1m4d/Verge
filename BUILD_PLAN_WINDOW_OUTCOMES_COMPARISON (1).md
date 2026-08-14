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

## Phase 1.5 — Frontend: A Readiness Panel, Not Just Raw Data

**Goal:** a glanceable answer to "is Phase 2 ready to run yet," sitting
in the Window Observations tab already built — not a reason to open
Supabase directly.

**1.5.1 — A small readiness endpoint**
How: `GET /api/window-outcomes/readiness?duration=15m` — a single-purpose
summary, not a data dump:
```python
@app.route("/api/window-outcomes/readiness")
@require_secret
def window_outcomes_readiness():
    duration = request.args.get("duration", "15m")
    client = db.get_client()
    windows_with_outcome = client.table("window_outcomes") \
        .select("market_window_start", count="exact") \
        .eq("market_duration", duration).execute()
    count = windows_with_outcome.count or 0
    threshold = 300  # matches the "a few hundred" floor from Phase 2
    return jsonify({
        "resolved_window_count": count,
        "threshold": threshold,
        "ready": count >= threshold,
        "pct_complete": min(100, round(100 * count / threshold, 1)),
    })
```
Done when: the endpoint returns a correct count and an honest `ready`
boolean, verified against a manual count in Supabase at least once.

**1.5.2 — Show it in the existing Window Observations tab**
How: A small, quiet strip at the top of the tab already built for
observations — not a new page, not something you have to go looking for.
Something like: `Capture-point comparison: 118 / 300 windows (39%) — not ready yet`,
flipping to a clear "Ready to run" state once `ready: true`. Reuses the
same tab you're already checking regularly for the timeline chart, so
this doesn't add a new place to remember to look.
Done when: opening the tab you already use tells you, at a glance,
whether Phase 2 is worth running yet — no separate check, no Supabase
console, no asking me to look it up for you.

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
rather than duplicated logic per caller. Put these in their own module
(e.g. `capture_point_rules.py`) so the API endpoint below and any future
standalone use both call the same logic, never two copies drifting apart:
```python
def rule_first_tick(ticks): ...        # today's actual default — the baseline to beat
def rule_fixed_checkpoint(ticks, seconds): ...   # decision at a specific fixed point, several values worth trying
def rule_strongest_score(ticks): ...   # whichever tick had the largest |score| anywhere in the window
def rule_late_checkpoint(ticks, min_gap, max_price): ...  # Late Entry V3's own logic, reusing its guardrails
```
Done when: each rule runs cleanly against the Phase 2.1 dataset and
returns exactly one decision per window.

**2.3 — An on-demand comparison endpoint, not a script you have to run locally**
How: `GET /api/capture-point-comparison?duration=15m` — assembles the
Phase 2.1 dataset, runs every rule from 2.2 against it, scores each
against `window_outcomes` with the same fee-adjusted edge math used
everywhere else in Verge, and returns the result as JSON. Gated by the
same readiness threshold as the Phase 1.5 panel — no point computing (or
looking at) a comparison built on too little data:
```python
@app.route("/api/capture-point-comparison")
@require_secret
def capture_point_comparison():
    duration = request.args.get("duration", "15m")
    client = db.get_client()

    count = client.table("window_outcomes").select("market_window_start", count="exact") \
        .eq("market_duration", duration).execute().count or 0
    if count < 300:
        return jsonify({"ready": False, "resolved_window_count": count, "threshold": 300})

    dataset = assemble_comparison_dataset(client, duration)  # Phase 2.1
    rules = {
        "first_tick": rule_first_tick,
        "checkpoint_2min": lambda t: rule_fixed_checkpoint(t, 120),
        "checkpoint_5min": lambda t: rule_fixed_checkpoint(t, 300),
        "strongest_score": rule_strongest_score,
        "late_entry": lambda t: rule_late_checkpoint(t, min_gap=0.30, max_price=0.92),
    }
    results = {name: score_rule_against_dataset(fn, dataset) for name, fn in rules.items()}
    return jsonify({"ready": True, "window_count": count, "results": results})
```
Each result entry: `{win_rate, roi_pct, trade_count}`. This runs live on
request rather than needing a pre-computed cache — a few hundred windows
is small enough that recomputing per call should stay fast; worth adding
caching later only if that stops being true.
Done when: hitting this endpoint with enough data returns a clean
comparison across all five rules; hitting it with too little data returns
`ready: false` instead of a misleading result.

**2.4 — A comparison panel in the frontend, sitting next to the readiness strip**
How: In the same Window Observations tab as the Phase 1.5 readiness
indicator — once `ready: true`, render a simple results table: one row
per rule, columns for win rate / ROI / trade count, with the
`first_tick` row visually pinned or highlighted as the reference line
every other rule is being measured against (matches the same principle
from 2.5 below — this shouldn't read as "which is best," it should read
as "does anything beat what we're actually doing today"). While not
ready, show the same progress readout as the Phase 1.5 panel instead of
an empty or broken table.
Done when: opening the tab shows either the readiness progress or, once
ready, a clear side-by-side table — no separate tool, no local script, no
asking me to run it for you.

**2.5 — Promote only if a rule genuinely, clearly wins**
How: Same discipline as divergence, Fear & Greed, and ATR before it —
nothing changes in the live engine just because a rule's row looks good
in this table. If a candidate clearly and consistently outperforms
`first_tick`, that's the point to actually change what triggers the
official decision in `_generate_signal_inner` — replacing the current
"whichever tick happened to run first" behavior with the winning rule,
deliberately. If nothing clearly beats it, that's a complete, useful
answer too: it means the current default, accidental as its origin was,
turned out fine — and now the table tells you that, instead of you having
to assume it.
