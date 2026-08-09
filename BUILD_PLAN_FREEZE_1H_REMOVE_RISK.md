# Build Plan: Freeze 1H & Remove Risk Mode (Addendum)

Companion to the existing Verge build plans. Two changes, sequenced so
neither can silently corrupt data or break the running tool: freeze the
1-hour path behind a reversible, frontend-accessible switch, and fully
remove Risk Mode and everything attached to it. 7 phases — read Phase 1
before touching any code, since it sets the safety rules the rest follows.

---

## Phase 1 — Decisions Before Any Code Changes

**Goal:** decide what happens to existing data before anything gets
deleted or stopped, so nothing is lost by accident.

**1.1 — Decide what happens to accumulated Risk Mode data**
How: Don't hard-delete the `mode='risk'` rows in `signals`/`paper_trades`
as part of this work. They cost nothing to leave in place, and they're
the only record of what that experiment actually showed so far — worth
keeping even if you're not acting on it further. Recommended: leave the
rows exactly where they are, just stop the pipeline from creating new
ones (Phase 4). If you want them gone later, that's a deliberate, separate
decision once you're sure you don't want to glance back at them.
Done when: you've consciously decided this rather than having it fall out
of a code change — "keep, stop generating" is the default here unless you
have a specific reason to purge.

**1.2 — Confirm no risk-mode trade is abandoned mid-resolution**
How: Before flipping off risk-mode generation, check
`/api/stats?mode=risk` for any trades with `resolved_outcome` still null.
Resolution should keep running for existing unresolved trades regardless
of what happens in Phase 4 (see 4.1's note) — but it's worth knowing going
in whether there's a backlog, so a stalled resolution doesn't look like a
mystery later.
Done when: you know the exact count of unresolved risk-mode trades at the
moment you start this work.

---

## Phase 2 — Freeze Mechanism, Backend

**Goal:** a single, reversible switch per duration, readable at runtime
with no redeploy needed to flip it.

**2.1 — Add a frozen-durations flag to the settings table**
How: Using the same `settings` key-value table already in place (from the
free sim/live toggle work), add a new row:
`key='frozen_durations', value='[]'` (a JSON array as text, e.g. `'["1h"]'`
once frozen). One flag, extensible to any future duration, not a
hardcoded boolean tied only to "1h."
Done when: you can read and write this row from a local script against
your real Supabase project.

**2.2 — Add small read/write helpers**
How:
```python
import json

def get_frozen_durations(client) -> set[str]:
    row = client.table("settings").select("value").eq("key", "frozen_durations").single().execute()
    return set(json.loads(row.data["value"])) if row.data else set()

def set_duration_frozen(client, duration: str, frozen: bool) -> None:
    current = get_frozen_durations(client)
    if frozen:
        current.add(duration)
    else:
        current.discard(duration)
    client.table("settings").update({"value": json.dumps(sorted(current))}).eq("key", "frozen_durations").execute()
```
Done when: calling `set_duration_frozen(client, "1h", True)` then
`get_frozen_durations(client)` returns `{"1h"}`, and unfreezing reverses it.

**2.3 — Gate the heartbeat on this flag, per duration**
How: At the top of the heartbeat's per-duration loop, check membership
before doing anything else — market discovery, price fetch, and scoring
should never run at all for a frozen duration, not just have their result
discarded:
```python
frozen = get_frozen_durations(client)
for duration in MARKET_CONFIGS:
    if duration in frozen:
        log.info(f"[{duration}] Frozen — skipping signal generation")
        results[duration] = {"status": "frozen"}
        continue
    # existing generate/persist/alert logic unchanged
```
Done when: freezing "1h" and running a heartbeat cycle produces zero new
`signals` rows for `market_duration='1h'`, while 15m continues exactly as
before.

**2.4 — Keep resolution running regardless of frozen status**
How: `resolve_previous_window()` should NOT check the frozen flag —
any 1-hour trade that was already open when you froze it still deserves to
be resolved against its real outcome, not abandoned. This is the one place
frozen and active durations should behave identically.
Done when: a trade that was live at the moment of freezing still resolves
correctly on a subsequent heartbeat, with `resolved_outcome` populated.

**2.5 — Add the admin endpoint**
How: A small protected route:
```python
@app.route("/api/admin/freeze", methods=["POST"])
@require_secret
def freeze_duration():
    duration = request.args.get("duration")
    frozen = request.args.get("frozen", "true").lower() == "true"
    if duration not in MARKET_CONFIGS:
        return jsonify({"error": "unknown duration"}), 400
    client = db.get_client()
    set_duration_frozen(client, duration, frozen)
    return jsonify({"duration": duration, "frozen": frozen})
```
Done when: `POST /api/admin/freeze?duration=1h&frozen=true` flips the flag
and returns the new state, protected by the same shared secret as
everything else.

---

## Phase 3 — Freeze Control, Frontend

**Goal:** the part you actually asked for — something you can flip from
the dashboard itself, not a database console.

**3.1 — Add a lock toggle to the duration tab**
How: Next to the existing "1H" tab control, a small lock icon/switch
(e.g. 🔓/🔒) that calls the Phase 2.5 endpoint on click and re-renders.
The "1H" tab itself stays clickable either way — freezing shouldn't hide
your historical data, just stop new data from being added to it.
Done when: clicking the lock toggles frozen state and the icon updates to
match, without a page reload.

**3.2 — Show a clear frozen indicator when viewing 1H**
How: When `frozen_durations` includes the currently-viewed duration,
render a small banner or badge — "1H is frozen · showing historical data
only, no new signals" — somewhere near the top of the dashboard, not
buried. The decision band, evidence band, etc. can either show the last
real signal generated before freezing (clearly timestamped) or a neutral
"frozen" state — either is fine, just don't show something that looks live
when it isn't.
Done when: looking at the frozen duration makes it immediately obvious,
without having to notice the small lock icon specifically.

**3.3 — Confirm stats and history remain fully browsable while frozen**
How: `/api/stats?duration=1h` and the signal log should work exactly as
before — freezing only affects generation, not read access to anything
already accumulated.
Done when: every existing 1H chart, stat, and log entry still renders
correctly while frozen.

---

## Phase 4 — Stop Risk Mode Generation (First, Least Invasive Step)

**Goal:** immediately stop new risk-mode complexity without yet touching
the rest of the codebase — the safest possible first move.

**4.1 — Remove the risk-signal calls from the heartbeat**
How: In the heartbeat's per-duration loop, delete the block that calls
`generate_risk_signal(sig)` and `persist_signal(risk_sig)`. Leave
`generate_risk_signal()` itself in `engine.py` untouched for now — Phase 6
removes the function properly, once you've confirmed nothing broke from
just silencing its caller.
Done when: a heartbeat cycle produces no new `mode='risk'` rows in either
table, while safe-mode signals continue exactly as before.

**4.2 — Confirm no other caller still reaches the risk path**
How: Recall from the earlier bug review that `/api/signal?mode=risk` had
its own path into risk logic (the bug where it silently mislabeled a
safe-mode signal). Whether or not you applied that fix, this endpoint
should now simply not be reachable in any meaningful way — Phase 5 removes
the frontend's ability to call it with `mode=risk` at all.
Done when: searching your codebase for `mode="risk"` or `mode == "risk"`
turns up only the (now-unused) `generate_risk_signal` function itself and
the DB-level filtering logic — nothing actively invoking it.

---

## Phase 5 — Remove Risk Mode UI

**Goal:** the dashboard stops offering something that no longer does
anything, cleanly.

**5.1 — Remove the mode toggle buttons**
How: Delete the SAFE/RISK button pair and the `currentMode` state/logic
that threads `mode=` into every fetch call (`/api/signal`, `/api/stats`,
`/api/performance`, `/api/signal-log`). Every one of those calls goes back
to being duration-only, matching how they worked before Risk Mode existed.
Done when: no request the frontend makes includes a `mode` query parameter
anywhere.

**5.2 — Remove risk-specific styling and indicators**
How: The `.confidence-badge.forced` CSS rule, the mode-indicator badge
("SAFE MODE"/"RISK MODE"), and any risk-specific colors or copy can all
go — none of it has anything left to render once 5.1 is done.
Done when: a search for "risk" (case-insensitive) in `frontend/index.html`
turns up nothing except maybe unrelated words like "riskier" in prose, if
any exists.

---

## Phase 6 — Full Backend Cleanup

**Goal:** remove the now-dead code properly, once Phase 4 has proven
stable for a few days with nothing depending on it.

**6.1 — Remove `generate_risk_signal()` and the `mode` parameter from `generate_signal()`**
How: `generate_signal()` goes back to a duration-only signature. Delete
`generate_risk_signal()` entirely from `engine.py`.
Done when: `engine.py` compiles with these functions gone and nothing
else in the codebase references them (a repo-wide search confirms it).

**6.2 — Simplify the DB layer's mode-scoping logic**
How: `get_stats()`'s safe-mode isolation logic (the block that always
computes the graduation gate from `mode='safe'` regardless of the passed
filter) can be simplified back to not needing a mode concept at all, since
every remaining row will always be `mode='safe'` going forward. You can
either simplify this now or leave the scoping logic in place as harmless
extra safety — it costs nothing to keep a gate that's redundant, and
removing it gains you very little. Lower priority than 6.1.
Done when: you've made a deliberate call either way, not left it half-done.

**6.3 — Remove or repurpose the risk-mode isolation test**
How: The unit test that seeds fake winning risk-mode trades and asserts
the graduation gate stays locked no longer has anything to test once risk
mode can't be generated at all. Delete it, rather than leaving a test that
imports a function that no longer exists — a broken import here would
fail your whole test suite for an unrelated reason if left dangling.
Done when: `pytest` runs clean with no import errors and no test
referencing risk mode remains.

**6.4 — Remove any risk-mode-specific Telegram logic**
How: If a risk-mode digest or alert-suppression branch was built (per the
earlier plan's Phase 4.2/5.3), remove it — Telegram logic goes back to
alerting only on safe-mode BET decisions, which is what it always should
have been doing exclusively from here on.
Done when: `telegram.py` has no remaining reference to risk mode.

---

## Phase 7 — Optional: Database Cleanup

**Goal:** genuinely optional, deferred cleanup — not required for the
tool to work correctly, only for tidiness.

**7.1 — Decide whether to drop the `mode` column**
How: Leaving `mode` on `signals`/`paper_trades` costs nothing — every new
row will simply always be `'safe'`. Dropping it via
`ALTER TABLE ... DROP COLUMN mode` is a real, slightly destructive schema
change for very little practical benefit. I'd leave it, but it's your call.
Done when: decided, not defaulted.

**7.2 — Decide the final fate of historical risk-mode rows**
How: Per Phase 1.1, these should already be untouched at this point.
If you want to export them for your own records before eventually purging
them (or just to satisfy curiosity about what the experiment showed, even
incomplete), a simple `SELECT * FROM paper_trades WHERE mode='risk'`
exported to CSV is all that's needed. Purging is optional and irreversible
— treat it as a separate, deliberate action, not a cleanup step to rush
through here.
Done when: you have what you want to keep, if anything, before any
eventual deletion.
