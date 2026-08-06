"""Explore hourly BTC Up/Down series."""
import json
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# Get hourly markets
print("=== Hourly BTC Up/Down markets ===")
r = requests.get(f"{GAMMA}/events", params={
    "limit": 10,
    "series_slug": "btc-up-or-down-hourly",
    "closed": "true",
}, timeout=30)
if r.ok:
    events = r.json()
    print(f"Found {len(events)} events")
    for e in events[:5]:
        slug = e.get("slug", "?")
        markets = e.get("markets", [])
        print(f"\n  {slug}")
        for m in markets[:1]:
            q = m.get("question", "")
            tokens_raw = m.get("clobTokenIds", "[]")
            if isinstance(tokens_raw, str):
                tokens = json.loads(tokens_raw)
            else:
                tokens = tokens_raw
            print(f"    Q: {q[:60]}")
            print(f"    Tokens: {tokens[:1]}")
            print(f"    Closed: {m.get('closed')}")

            # Try prices-history
            if tokens:
                r2 = requests.get(f"{CLOB}/prices-history", params={
                    "market": tokens[0],
                    "interval": "max",
                    "fidelity": 5,
                }, timeout=30)
                if r2.ok:
                    hist = r2.json().get("history", [])
                    print(f"    History points: {len(hist)}")
                    if hist:
                        print(f"    First: {hist[0]}")
                        print(f"    Last: {hist[-1]}")
