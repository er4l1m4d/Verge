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
6 phases.

---

## Phase 1 — Database: Extend Existing Tables

**Goal:** add source-labeling and quality visibility without touching
anything already load-bearing.

**1.1 — Migration 009: Add columns to `price_snapshots` and `signals`**
How:
```sql
-- price_snapshots: quality tracking
ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS quality_note TEXT;
ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS age_ms INTEGER;

-- signals: source labeling
ALTER TABLE signals ADD COLUMN IF NOT EXISTS price_source TEXT;
```
Done when: migration runs cleanly, existing writes to both tables still
succeed unchanged, new writes can optionally populate the new columns.

**1.2 — Update `db.py` dataclasses**
How: add `quality_note: str | None = None` and `age_ms: int | None = None`
to `PriceSnapshotRow`. Add `price_source: str | None = None` to `SignalRow`.
Done when: dataclasses match new schema, existing code compiles.

---

## Phase 2 — Source Labels on Every Price

**Goal:** every signal record can answer "which source actually supplied
this price" without guessing.

**2.1 — Add `write_price_snapshot_sync()` to `db.py`**
How: synchronous wrapper around the existing `write_price_snapshot()` that
the TWAP accumulator thread will call (since it runs in a background
thread, not async):
```python
def write_price_snapshot_sync(source: str, symbol: str, price: float,
                               timestamp_ms: int, quality_note: str | None = None) -> int | None:
    row = PriceSnapshotRow(source=source, symbol=symbol, price=price,
                           timestamp_ms=timestamp_ms, quality_note=quality_note)
    return write_price_snapshot(get_client(), row)
```
Done when: function exists and can be called from a thread.

**2.2 — Label source on every `persist_signal()` call**
How: in `engine.py`, when building the signal dict before insert, set
`price_source` to whichever source supplied `current_price`. The source
chain in `_generate_signal_inner()` already tries Polymarket WS →
CoinGecko → candle close — just capture which one succeeded and pass it
through to the insert.
Done when: every signal row in Supabase has a non-null `price_source`.

---

## Phase 3 — TWAP Accumulator Thread

**Goal:** fix the actual gap — a single WS tick isn't the same shape of
number as a 60-second average, even when sourced from the right feed.
This closes that gap, cloud-side, using the same background-thread pattern
already running in production for the Telegram `/start` listener.

**3.1 — A tick accumulator thread, started once at process boot**
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
**Same honest caveat as the Telegram listener:** this thread's continuous
operation leans on the heartbeat's side effect of keeping Render awake,
not a guaranteed contract.
Done when: `price_snapshots` accumulates `polymarket_ws_tick` rows roughly
continuously, not just once per heartbeat.

**3.2 — A real TWAP calculator, correctly handling partial windows**
How: naive `sum(price) / count` is wrong if ticks aren't evenly spaced,
which they won't be:
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
the whole window, and a tick that started before the window.

**3.3 — Add Pyth oracle fallback**
How: add `get_pyth_btc_price_value()` to a new `backend/pyth_fetcher.py`
using the free Pyth oracle API (`https://api.pyth.network/price_feeds`).
Wire it into the price chain between Chainlink and Coinbase.
Done when: Pyth returns a valid BTC price or None.

---

## Phase 4 — Wire TWAP into Price-Source Chain

**Goal:** under normal conditions, `polymarket_ws_twap_60s` is the source
actually firing on most cycles — with other tiers visibly available but
rarely needed.

**4.1 — Correct the price-source ordering**
How: closest-to-true-source first:
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
        return p, "pyth"
    return get_spot_price(), "coinbase_or_coingecko"
```
Done when: under normal conditions, `polymarket_ws_twap_60s` fires on most
cycles — confirmed via the Phase 2.2 labels.

**4.2 — Add `get_recent_price_snapshots()` to `db.py`**
How: query helper that reads recent ticks for TWAP calculation:
```python
def get_recent_price_snapshots(client, source: str, symbol: str,
                                since_ms: int, limit: int = 100) -> list[PriceSnapshotRow]:
    resp = (client.table("price_snapshots")
            .select("source,symbol,timestamp_ms,price")
            .eq("source", source)
            .eq("symbol", symbol)
            .gte("timestamp_ms", since_ms)
            .order("timestamp_ms", desc=False)
            .limit(limit)
            .execute())
    return [PriceSnapshotRow(**r) for r in (resp.data or [])]
```
Done when: function returns ticks for TWAP computation.

---

## Phase 5 — Backend: Diagnostics Endpoint

**Goal:** one endpoint that answers all price-source questions.

**5.1 — `GET /api/diagnostics` endpoint**
How: returns JSON with 5 sections:

```python
@app.route("/api/diagnostics", methods=["GET"])
@require_secret
def api_diagnostics():
    now_ms = int(time.time() * 1000)

    # 1. Source breakdown (last 24h)
    source_stats = db.get_source_breakdown(client, since_ms=now_ms - 86_400_000)

    # 2. Live prices from each source
    live = {}
    twap_ticks = db.get_recent_price_snapshots(client, "polymarket_ws_tick", "BTCUSD",
                                                since_ms=now_ms - 90_000)
    live["polymarket_ws_twap_60s"] = compute_twap(twap_ticks, now_ms)
    live["chainlink_onchain"] = get_chainlink_price()
    live["pyth"] = get_pyth_btc_price_value()
    live["coinbase_spot"] = get_spot_price()

    # 3. Recent signals with source
    recent_signals = db.get_recent_signals_with_source(client, limit=20)

    # 4. Resolution accuracy by source
    accuracy = db.get_resolution_accuracy_by_source(client)

    # 5. TWAP vs single-tick comparison
    last_tick = twap_ticks[-1].price if twap_ticks else None
    twap_vs_tick = {
        "twap": live["polymarket_ws_twap_60s"],
        "last_single_tick": last_tick,
        "difference_pct": abs(live["polymarket_ws_twap_60s"] - last_tick) / last_tick * 100
            if live["polymarket_ws_twap_60s"] and last_tick else None
    }

    return jsonify({
        "source_breakdown": source_stats,
        "live_prices": live,
        "recent_signals": recent_signals,
        "resolution_accuracy": accuracy,
        "twap_vs_tick": twap_vs_tick,
        "timestamp": now_ms
    })
```

**5.2 — Helper queries in `db.py`**
How: add these functions:
- `get_source_breakdown(client, since_ms)` — `SELECT source, AVG(price), COUNT(*) FROM price_snapshots WHERE timestamp_ms > $1 GROUP BY source`
- `get_recent_signals_with_source(client, limit)` — `SELECT id, timestamp, decision, current_price, price_source, strike_price FROM signals ORDER BY id DESC LIMIT $1`
- `get_resolution_accuracy_by_source(client)` — JOIN signals with window_outcomes on window start, group by price_source, count agreements
Done when: endpoint returns valid JSON with all 5 sections.

---

## Phase 6 — Frontend: Diagnostics Page

**Goal:** one page that answers all price-source questions at a glance.
Separate page at `/diagnostics`, data tables style (matching Verge design
system: dark background, amber accents, clean monospace tables).

**6.1 — Page container + navigation**
How: add `#diagnostics-page` div to `index.html`, add "Diagnostics" button
in header or timeline nav. Show/hide pattern matching existing pages.
Done when: page loads, shows loading state, fetches `/api/diagnostics`.

**6.2 — Source Breakdown table**
How: table showing `source | avg_price | count | last_24h`. Highlights the
primary source (TWAP) with amber accent.
Done when: table renders with real data.

**6.3 — Live Prices panel**
How: key-value display of each source's current price. Green if TWAP is
active, grey if fallback.
Done when: prices update on page load.

**6.4 — Recent Signals table**
How: table showing last 20 signals with `id | time | decision | price |
source | strike`. Clickable rows link to signal detail modal.
Done when: rows render, links work.

**6.5 — Resolution Accuracy table**
How: table showing `source | total | agreements | accuracy_pct`. Shows
how often each source matched Polymarket's actual resolution.
Done when: table renders (may be empty if not enough resolved windows yet).

**6.6 — TWAP vs Single-Tick panel**
How: side-by-side display: TWAP value | last tick | difference %.
Amber if difference > 0.1%, green if < 0.1%.
Done when: comparison renders.
Done when: all 6 panels render with real data.
