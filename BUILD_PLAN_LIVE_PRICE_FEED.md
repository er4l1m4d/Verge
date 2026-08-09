# Build Plan: Live Price Feed & Strike Fix (Addendum)

Companion to the existing Verge build plans. Sourced from
`FrondEnt/PolymarketBTC15mAssistant` — a much closer match to Verge's
actual problem than the previous comparison repo, and this one directly
addresses the price-to-beat discrepancy flagged earlier. 4 phases, ordered
by how directly each one fixes a known problem.

---

## Phase 1 — Robust Strike-Price Extraction

**Goal:** stop guessing one field path for the strike price; search for it
properly. This is the most likely direct fix for the mismatch you saw
between Verge's displayed price-to-beat and Polymarket's own.

**1.1 — Replace the single-path lookup with a recursive key search**
How: Replace `metadata.get("priceToBeat")` with a function that walks the
*entire* market JSON object — not just one assumed nested path — looking
for any key whose name matches `price`, `strike`, `threshold`, `target`,
or `beat` (case-insensitive), bounded to a reasonable recursion depth
(6 levels is plenty) with a "seen" set to guard against any circular
references:

```python
import re

_STRIKE_KEY_PATTERN = re.compile(r"(price|strike|threshold|target|beat)", re.I)

def extract_strike_from_market(market: dict, max_depth: int = 6) -> float | None:
    seen = set()
    stack = [(market, 0)]
    while stack:
        obj, depth = stack.pop()
        obj_id = id(obj)
        if not isinstance(obj, (dict, list)) or obj_id in seen or depth > max_depth:
            continue
        seen.add(obj_id)

        items = enumerate(obj) if isinstance(obj, list) else obj.items()
        for key, value in items:
            if isinstance(value, (dict, list)):
                stack.append((value, depth + 1))
                continue
            if not _STRIKE_KEY_PATTERN.search(str(key)):
                continue
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            # Sanity bound — a real BTC price, not some unrelated small number
            if 1_000 < n < 2_000_000:
                return n
    return None
```

Done when: running this against a live 15-minute market's raw JSON
(fetched via the curl command from the earlier debugging session) returns
a plausible BTC price, whatever the actual field name turns out to be.

**1.2 — Add the text-parsing fallback**
How: If 1.1 finds nothing, fall back to regex-parsing the strike directly
out of the market's `question` or `title` text, which appears to embed it
in human-readable form:

```python
_PRICE_TO_BEAT_TEXT = re.compile(
    r"price\s*to\s*beat[^\d$]*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I
)

def parse_strike_from_text(market: dict) -> float | None:
    text = str(market.get("question") or market.get("title") or "")
    m = _PRICE_TO_BEAT_TEXT.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None
```

Done when: on any market where 1.1's structured search comes up empty,
this fallback still recovers a correct strike from the visible market
title.

**1.3 — Wire both into the existing strike-resolution order**
How: `strike_price = extract_strike_from_market(market) or parse_strike_from_text(market) or <existing Coinbase/computed fallback>`.
Keep your existing fallback as the last resort, not removed — this just
inserts a much more reliable pair of steps before it.
Done when: your earlier diagnostic log line ("Using official Polymarket
strike" vs "using fallback") shows the *official* branch firing far more
often than it did before — ideally close to every cycle, since this
search is far less likely to miss the real field than a single guessed path.

---

## Phase 2 — Polymarket's Live Price Feed as Primary Source

**Goal:** replace the Chainlink-on-chain-approximation as your *primary*
15-minute price source with the literal feed Polymarket's own UI reads
from — only falling back to the on-chain read if this is unavailable.

**2.1 — Add a short-lived WebSocket read, once per heartbeat**
How: Not a persistent connection — open, subscribe, capture the first
matching BTC message, close, all within the same heartbeat call that
already does everything else for the 15-minute path. In Python, using the
`websockets` library:

```python
import asyncio
import json
import websockets

async def get_polymarket_chainlink_price(timeout_s: float = 5.0) -> tuple[float, int] | None:
    url = "wss://ws-live-data.polymarket.com"
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            await ws.send(json.dumps({
                "action": "subscribe",
                "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*", "filters": ""}],
            }))
            async with asyncio.timeout(timeout_s):
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("topic") != "crypto_prices_chainlink":
                        continue
                    payload = data.get("payload") or {}
                    symbol = str(payload.get("symbol") or payload.get("pair") or "").lower()
                    if "btc" not in symbol:
                        continue
                    price = payload.get("value") or payload.get("price")
                    ts = payload.get("timestamp") or payload.get("updatedAt")
                    if price is not None:
                        return float(price), int(float(ts) * 1000) if ts else None
    except Exception:
        return None
```

Add `websockets` to `requirements.txt`. Wrap the call with a firm timeout
(5 seconds is plenty) so a slow or unresponsive connection can't stall the
whole heartbeat cycle.
Done when: calling this function returns a live BTC price within a couple
of seconds, matching what's shown on Polymarket's own site at that moment.

**2.2 — Reorder the price-source priority for the 15m path**
How: New priority order — Polymarket live WS (2.1) first, existing
on-chain Chainlink RPC read second, Coinbase snapshot last. Update the
signal's `note` field to record which source actually supplied the price
this cycle, the same way you're already logging which strike-source fired.
Done when: under normal conditions the WS path is the one firing on most
cycles, with the on-chain and Coinbase paths visibly available as
fallbacks in the logs rather than silently unused code.

**2.3 — Re-validate the "price to beat differs from Polymarket" symptom**
How: After 2.1 and 2.2 are live, watch a handful of 15-minute windows and
compare Verge's displayed strike and current price against Polymarket's
own page side by side, the same manual check you were doing when you first
noticed the mismatch.
Done when: the two numbers agree, or any remaining gap is small enough to
be explained by normal seconds-level timing lag rather than a structural
mismatch.

---

## Phase 3 — Numeric Series ID as a Discovery Fallback

**Goal:** a third, more stable layer in market discovery, immune to any
future slug-naming changes.

**3.1 — Add series ID to market_config.py**
How: Add a `series_id` field to the 15m config entry (`10192`, per this
repo's default — worth a quick independent check against a live Gamma
query before trusting it, same discipline as everything else here) and
use it as an additional filter option alongside `series_slug` and
`slug_prefix` in your discovery chain: try `series_slug` first, then
`series_id` if that returns nothing, then `slug_prefix` matching as the
final fallback.
Done when: temporarily breaking the `series_slug` value still finds the
correct live market via `series_id`.

---

## Phase 4 — Optional: Proxy Support

**Goal:** a documented escape hatch, given you've already hit two
different geo-blocking issues in this project (Nigeria's ISP-level block
on Binance, and Binance's own block on US-hosted server IPs).

**4.1 — Add optional proxy support to your HTTP calls**
How: `requests` supports a `proxies=` dict read from standard
`HTTPS_PROXY`/`HTTP_PROXY` environment variables via
`requests.utils.get_environ_proxies()`, or simply pass
`proxies={"https": os.environ.get("HTTPS_PROXY")}` when set. Low priority
given switching Render's region already solved your actual production
issue — this is worth having documented as a fallback option, not
something to build out urgently.
Done when: setting `HTTPS_PROXY` in your environment routes outbound
calls through it, verified locally, and it's a no-op when unset.

---

## Not adopting from this repo

Their on-chain fallback's event-subscription pattern (`AnswerUpdated` over
a WSS RPC) — see the chat discussion for why polling `latestRoundData()`,
which Verge already does, better suits a brief-per-heartbeat connection
model than waiting on an event that might not fire during a short window.
Their additional indicators (Heiken Ashi, MACD, VWAP) aren't included here
either — same discipline as the last addendum: any new indicator earns a
weight through backtesting, not because another project uses it.
