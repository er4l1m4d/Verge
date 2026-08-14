# Build Plan: Price Reference Layer, Right-Sized (Addendum)

Companion to the existing Verge build plans. Everything in this plan runs
entirely on Render — your existing cloud backend — never on your own
computer. Nothing here depends on your electricity, your machine being
on, or anything local to you at all; "computed cloud-side" is the phrase
used throughout instead of "locally" to avoid the ambiguity from earlier.

Keeps the good principles from the larger draft plan reviewed this
session — explicit source labeling, never claim an estimate is official,
validate before promoting — scoped to what a free-tier, single-operator
tool actually needs. No persistent collector *process* separate from what
already exists, no new colliding tables, no dual-calculator replay engine.
4 phases.

---

## Phase 1 — Extend, Don't Replace, What Already Exists

**Goal:** add source-labeling and quality visibility without touching
anything already load-bearing. Unchanged from the prior version of this
plan.

**1.1 — Add columns to the existing `price_snapshots` table, don't redefine it**
How:
```sql
ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS quality_note TEXT;
ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS age_ms INTEGER;
```
Done when: existing writes to `price_snapshots` still succeed unchanged,
and new writes can optionally populate the two new columns.

**1.2 — Every price gets a source label, every time**
How: Wherever `current_price` or the strike price gets set, set a
`source` label alongside it, reusing the fallback-chain fix already
planned. No new infrastructure, just consistent labeling.
Done when: every signal record can answer "which source actually
supplied this price" without guessing.

---

## Phase 2 — A Real 60-Second TWAP, Computed Over Polymarket's Own Ticks

**Goal:** fix the actual gap flagged this session — a single WS tick
isn't the same shape of number as a 60-second average, even when it's
sourced from the right feed. This closes that gap, cloud-side, using the
same background-thread pattern already running in production for the
Telegram `/start` listener.

**2.1 — A tick accumulator thread, started once at process boot**
How: Same shape as `start_bot_listener()` — a background thread, started
once when `app.py` loads, that keeps a persistent-ish connection to
`wss://ws-live-data.polymarket.com`, subscribed to `crypto_prices_chainlink`,
and on every incoming BTC tick, writes it to `price_snapshots` tagged
`source="polymarket_ws_tick"`:
```python
def start_ws_tick_accumulator():
    def _run():
        while True:
            try:
                asyncio.run(_accumulate_ticks())
            except Exception as e:
                log.warning(f"WS tick accumulator dropped, reconnecting: {e}")
                time.sleep(5)
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    log.info("WS tick accumulator started")

async def _accumulate_ticks():
    async with websockets.connect("wss://ws-live-data.polymarket.com") as ws:
        await ws.send(json.dumps({
            "action": "subscribe",
            "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*", "filters": ""}],
        }))
        async for raw in ws:
            data = json.loads(raw)
            if data.get("topic") != "crypto_prices_chainlink":
                continue
            payload = data.get("payload") or {}
            if "btc" not in str(payload.get("symbol", "")).lower():
                continue
            price = payload.get("value") or payload.get("price")
            ts = payload.get("timestamp")
            if price is not None:
                db.write_price_snapshot_sync(source="polymarket_ws_tick", symbol="BTCUSD",
                                              price=float(price), timestamp_ms=int(ts))
```
**Same honest caveat as the Telegram listener, stated plainly rather than
buried:** this thread's continuous operation leans on the heartbeat's
side effect of keeping Render awake, not a guaranteed contract. Worth
pairing with the already-planned Telegram webhook migration — once that
lands, this becomes the *one* thing in the codebase depending on that
implicit assumption, not two independent features quietly sharing the
same fragility.
Done when: `price_snapshots` accumulates `polymarket_ws_tick` rows roughly
continuously, not just once per heartbeat.

**2.2 — A real TWAP calculator, correctly handling partial windows**
How: This is the one piece of rigor worth keeping from the larger plan in
full, not scoped down — a naive `sum(price) / count` is wrong if ticks
aren't evenly spaced, which they won't be:
```python
def compute_twap(ticks: list[dict], window_end_ms: int, window_seconds: int = 60) -> float | None:
    """ticks: [{'timestamp_ms': int, 'price': float}, ...], any order.
    Time-weights each price by how long it was the 'current' price within
    the window, correctly clipping the first tick's contribution if it
    started before the window began."""
    window_start_ms = window_end_ms - (window_seconds * 1000)
    ticks = sorted([t for t in ticks if t["timestamp_ms"] <= window_end_ms],
                    key=lambda t: t["timestamp_ms"])
    ticks = [t for t in ticks if t["timestamp_ms"] >= window_start_ms] or ticks[-1:]
    if not ticks:
        return None

    weighted_sum, total_weight = 0.0, 0.0
    for i, tick in enumerate(ticks):
        seg_start = max(tick["timestamp_ms"], window_start_ms)
        seg_end = ticks[i + 1]["timestamp_ms"] if i + 1 < len(ticks) else window_end_ms
        duration = max(0, seg_end - seg_start)
        weighted_sum += tick["price"] * duration
        total_weight += duration

    return weighted_sum / total_weight if total_weight > 0 else ticks[-1]["price"]
```
Done when: unit tests cover evenly-spaced ticks, a single tick spanning
the whole window, and a tick that started before the window (confirming
only its in-window portion counts) — the same edge cases the larger plan
correctly flagged as essential, just without a second independent
calculator to cross-check against.

**2.3 — Wire the TWAP into the price-source chain, correctly ordered**
How: Correcting the ordering from last session — closest-to-true-source
first:
```python
def get_current_price(duration_config) -> tuple[float | None, str]:
    recent_ticks = db.get_recent_price_snapshots(source="polymarket_ws_tick",
                                                   symbol="BTCUSD", since_ms=now_ms() - 90_000)
    twap = compute_twap(recent_ticks, window_end_ms=now_ms()) if len(recent_ticks) >= 3 else None
    if twap:
        return twap, "polymarket_ws_twap_60s"
    if (p := get_chainlink_price()):
        return p, "chainlink_onchain"
    if (p := get_pyth_btc_price_value()):
        return p, "pyth"  # different oracle network entirely — last resort before raw exchange spot
    return get_spot_price(), "coinbase_or_coingecko"
```
Done when: under normal conditions, `polymarket_ws_twap_60s` is the
source actually firing on most cycles — confirmed via the Phase 1.2
labels — with the other tiers visibly available but rarely needed.

---

## Phase 3 — Lightweight Multi-Source Comparison (No New Tables)

**Goal:** unchanged in spirit from before — measure, don't assume.

**3.1 — A simple comparison query**
How:
```sql
SELECT source, AVG(price) as avg_price, COUNT(*) as n
FROM price_snapshots
WHERE symbol = 'BTCUSD' AND timestamp_ms > (extract(epoch from now()) * 1000 - 86400000)
GROUP BY source;
```
Done when: you can answer "how much do these sources typically disagree"
without needing a dedicated dashboard for it.

**3.2 — The question this whole plan exists to answer**
How: Once real `window_outcomes` data exists alongside real
`polymarket_ws_twap_60s` history, directly check: does Verge's computed
strike/current-price now agree with what Polymarket actually resolved to,
more often than the single-tick version did? That comparison — not
reasoning about it in the abstract — is what confirms or corrects
everything in Phase 2.
Done when: you have a real, measured answer instead of an inference.

---

## Phase 4 — Frontend: One Line, Not a New Panel

**Goal:** unchanged — visibility without new UI surface area.

**4.1 — Add the source label to the existing price display**
How: `$67,218.42 (polymarket_ws_twap_60s)` next to wherever the price
already renders.
Done when: glancing at the dashboard tells you which source and which
computation is actually live right now.
