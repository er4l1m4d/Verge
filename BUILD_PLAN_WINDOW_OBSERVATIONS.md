# Build Plan: Window Observation Logging (Addendum)

Companion to the existing Verge build plans. Adds continuous within-window
snapshots — odds, indicators, score — logged throughout each 15-minute
window, not just at the single moment a window opens. Purely observational:
does not create paper trades, does not touch the graduation gate, does not
alert. Also includes the small, unrelated `odds: 0` fallback fix flagged
alongside this. 7 phases.

**Isolation guarantee, stated once up front since it governs every phase
below:** nothing in this plan writes to `signals` or `paper_trades`, calls
`persist_signal()`'s write path, or is read by `get_stats()` or the
graduation gate. It's a new table, new endpoints, and a new frontend
section — additive, not interleaved with anything already running.

---

## Phase 1 — Quick Fix: The `odds: 0` Fallback (Independent, Do First)

**Goal:** a small, unrelated bug, worth clearing before it's forgotten
under the bigger work below.

**1.1 — Find where a failed odds fetch becomes `0` instead of `None`/`0.50`**
How: `get_current_odds()` itself only ever returns a valid `(0, 1)` float
or `None` — confirmed by reading it directly. So the bare `0` seen in
the data is happening downstream, wherever `odds` gets assigned from the
result of that call. Search for the assignment (likely something like
`odds = get_current_odds(token_id) or 0` — the `or 0` pattern is the
likely culprit, since `or` treats `None` correctly but would also
incorrectly collapse a legitimately falsy-but-valid value if one existed).
Replace with an explicit check: `odds = get_current_odds(token_id); if odds is None: odds = 0.50`.
Done when: a forced fetch failure produces `odds: 0.5` in the resulting
signal, never `odds: 0`.

---

## Phase 2 — New Table for Continuous Observations

**Goal:** storage for a real per-window time series, structurally separate
from the once-per-window `signals` table.

**2.1 — Add the table**
How:
```sql
CREATE TABLE IF NOT EXISTS window_observations (
    id BIGSERIAL PRIMARY KEY,
    market_duration TEXT NOT NULL,
    market_window_start BIGINT NOT NULL,
    seconds_into_window INTEGER NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    odds NUMERIC,
    current_price NUMERIC,
    strike_price NUMERIC,
    rsi NUMERIC,
    ma_signal INTEGER,
    volume_signal INTEGER,
    divergence_signal INTEGER,
    fear_greed_value INTEGER,
    score NUMERIC,
    hypothetical_decision TEXT
);
CREATE INDEX IF NOT EXISTS idx_window_obs_window
    ON window_observations(market_duration, market_window_start);
```
No foreign key to `signals` or `paper_trades` — deliberately independent,
so nothing about this table's existence or growth can affect queries
against the tables the graduation gate reads from.
Done when: the table exists and a manual insert/select round-trips
correctly.

---

## Phase 3 — Log Every Heartbeat Tick, Unconditionally

**Goal:** the actual fix to the blind spot — no idempotency gate here, on
purpose.

**3.1 — Add an always-write observation step to the heartbeat**
How: `generate_signal()` already runs on every heartbeat tick regardless
of whether `persist_signal()` ends up actually writing anything (the
idempotency check happens inside `persist_signal`, after the signal's
already been computed) — so this piggybacks on work already being done,
no new API calls needed:
```python
sig = generate_signal(duration)
log_window_observation(client, sig)   # NEW — always writes, no gate
persist_signal(sig)                   # EXISTING — gated, unchanged
```
```python
def log_window_observation(client, sig: LiveSignal) -> None:
    window_start = sig.hour_open_time or 0
    seconds_in = max(0, int(time.time() * 1000 - window_start) // 1000)
    client.table("window_observations").insert({
        "market_duration": sig.duration,
        "market_window_start": window_start,
        "seconds_into_window": seconds_in,
        "odds": sig.odds,
        "current_price": sig.current_price,
        "strike_price": sig.strike_price,
        "rsi": sig.rsi,
        "ma_signal": sig.ma_signal,
        "volume_signal": sig.volume_signal,
        "divergence_signal": sig.divergence_signal,
        "fear_greed_value": sig.fear_greed_value,
        "score": sig.score,
        "hypothetical_decision": sig.final_decision,
    }).execute()
```
Wrap this in its own try/except, logged but non-fatal — a failure to log
an observation should never be able to interrupt the real signal/persist
flow that follows it.
Done when: a single 15-minute window, once fully elapsed, shows roughly
one `window_observations` row per heartbeat interval (e.g. ~15 rows at a
1-minute cadence) — not just the single row `signals` would show for that
same window.

**3.2 — Confirm zero effect on the existing track**
How: After deploying, check that `signals` and `paper_trades` row counts
and content are completely unaffected — same one-row-per-window behavior
as before, same graduation gate numbers.
Done when: `/api/stats?mode=safe` shows no change in behavior purely from
this deployment, only `window_observations` growing.

---

## Phase 4 — Read API for Observations

**Goal:** a way to pull a window's full timeline, separate from every
existing endpoint.

**4.1 — Endpoint for one window's full observation timeline**
How: `GET /api/window-observations?duration=15m&window_start=<ms>` —
returns every logged row for that window, ordered by `seconds_into_window`.
```python
@app.route("/api/window-observations")
@require_secret
def window_observations():
    duration = request.args.get("duration", "15m")
    window_start = request.args.get("window_start", type=int)
    client = db.get_client()
    query = client.table("window_observations").select("*").eq("market_duration", duration)
    if window_start:
        query = query.eq("market_window_start", window_start)
    else:
        # default: most recent window with any observations
        latest = query.order("market_window_start", desc=True).limit(1).execute()
        if latest.data:
            window_start = latest.data[0]["market_window_start"]
            query = query.eq("market_window_start", window_start)
    result = query.order("seconds_into_window").execute()
    return jsonify(result.data)
```
Done when: calling this with no `window_start` returns the current or
most recently completed window's full timeline; calling it with a
specific `window_start` returns that window's timeline.

**4.2 — Endpoint listing recent windows with data available**
How: `GET /api/window-observations/recent?duration=15m&limit=10` —
distinct `market_window_start` values, most recent first, for populating
a picker in the frontend.
Done when: returns a clean list of recent window-start timestamps with at
least one observation each.

---

## Phase 5 — Frontend: A Genuinely Separate View

**Goal:** visible and useful, but nowhere near the existing decision band
— matches the "separated so it doesn't clash" requirement directly.

**5.1 — A new, distinct tab or section — not layered into the existing dashboard**
How: Something like a "Timeline" tab sitting alongside the existing
duration tabs, not a widget bolted onto the current decision view. Landing
here should feel like a different mode of looking at the tool — inspecting
history, not watching the live decision.
Done when: navigating to it doesn't alter or share visual space with the
decision/evidence/probability bands used elsewhere.

**5.2 — A window picker**
How: A simple dropdown or recent-windows list, populated from 4.2, letting
you select which window's timeline to inspect. Default to the most recent
window with data.
Done when: switching windows in the picker reloads the timeline for that
window specifically.

**5.3 — The timeline itself**
How: A simple line chart — `seconds_into_window` on the X axis, with
toggleable series for odds, score, and RSI (the three most likely to show
interesting movement). A vertical marker at whatever second the *official*
per-window signal was actually captured (near-zero, per what's been
observed) makes explicit just how early that single snapshot is relative
to the rest of the window's life — this is the visual that actually
answers the question this whole plan exists to investigate.
Done when: picking a window with a full set of observations renders a
readable chart showing how odds/score/RSI actually moved across that
window's 900 seconds.

---

## Phase 6 — Data Hygiene

**Goal:** this table grows fast (roughly one row per minute per active
window) — worth a retention plan from the start rather than after it's a
problem.

**6.1 — Add a simple retention rule**
How: A scheduled cleanup (piggyback on the existing heartbeat, gated to
run once a day) deleting `window_observations` rows older than a fixed
window — 30 days is a reasonable default, comfortably more than enough
for the Late Entry analysis this data exists to support, while keeping
Supabase's free-tier storage in check.
Done when: rows older than the retention period are actually being
cleared, verified by checking the oldest remaining row's timestamp
periodically.

---

## Phase 7 — What This Unlocks (Not Built Yet, Just Noting the Path)

Once a few weeks of real observation data exists, that's the point to come
back to the Late Entry V3 and ATR work from the previous plan — except now
testable directly against Verge's own real captured behavior instead of
only a historical approximation: does the score/odds gap at, say, minute
10 into a window actually predict outcomes better than the current
near-instant snapshot does? That comparison is what determines whether the
*official* decision point should ever move — a deliberate call made with
real evidence in hand, not a side effect of infrastructure nobody was
watching.
