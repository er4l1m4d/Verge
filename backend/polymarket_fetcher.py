"""Polymarket historical odds fetcher — Phase 1.3."""
import json
import time

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


def search_hourly_btc_markets(
    series_slug: str = "btc-up-or-down-daily",
    limit: int = 100,
) -> list[dict]:
    """Find past BTC Up/Down markets via Gamma API.

    Uses the series_slug parameter to filter to the right product line.
    Each event contains one market with two outcome tokens (Up/Down).

    Returns:
        List of dicts with keys: event_slug, market_id, condition_id,
        clob_token_ids, question, end_date, active, closed.
    """
    results = []
    offset = 0
    page_size = min(limit, 100)

    while len(results) < limit:
        params = {
            "limit": page_size,
            "offset": offset,
            "series_slug": series_slug,
            "closed": "true",
        }
        try:
            resp = requests.get(f"{GAMMA_BASE}/events", params=params, timeout=30)
            resp.raise_for_status()
            events = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"  Warning: Gamma API request failed: {e}")
            break

        if not events:
            break

        for event in events:
            for market in event.get("markets", []):
                # clobTokenIds is a JSON string, not a list
                tokens_raw = market.get("clobTokenIds", "[]")
                if isinstance(tokens_raw, str):
                    try:
                        tokens = json.loads(tokens_raw)
                    except json.JSONDecodeError:
                        tokens = []
                else:
                    tokens = tokens_raw

                results.append({
                    "event_slug": event.get("slug", ""),
                    "market_id": market.get("id"),
                    "condition_id": market.get("conditionId"),
                    "clob_token_ids": tokens,
                    "question": market.get("question", ""),
                    "end_date": market.get("endDate"),
                    "active": market.get("active", False),
                    "closed": market.get("closed", False),
                })

                if len(results) >= limit:
                    break

        offset += page_size
        time.sleep(0.1)

    return results


def get_prices_history(
    token_id: str,
    interval: str = "max",
    fidelity: int = 5,
) -> list[dict]:
    """Fetch historical price (odds) data from CLOB API.

    Args:
        token_id: The CLOB token ID (from clobTokenIds).
        interval: "1h", "6h", "1d", "1w", "1m", or "max".
        fidelity: Data point spacing in minutes.

    Returns:
        List of dicts with keys: timestamp, price.
    """
    params = {
        "market": token_id,
        "interval": interval,
        "fidelity": fidelity,
    }
    resp = requests.get(f"{CLOB_BASE}/prices-history", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    history = data.get("history", [])
    return [{"timestamp": p.get("t"), "price": float(p.get("p", 0))} for p in history]


def fetch_market_odds(
    token_id: str,
    interval: str = "max",
    fidelity: int = 5,
) -> list[dict]:
    """Convenience wrapper: fetch odds for a single token, with error handling."""
    try:
        return get_prices_history(token_id, interval, fidelity)
    except requests.exceptions.RequestException as e:
        print(f"  Warning: failed to fetch odds for {token_id[:16]}...: {e}")
        return []


if __name__ == "__main__":
    print("Searching for BTC Up/Down markets (daily series)...")
    markets = search_hourly_btc_markets(limit=5)
    print(f"  Found {len(markets)} markets")

    for m in markets[:3]:
        token_ids = m["clob_token_ids"]
        if not token_ids:
            print(f"  Skipping {m['question'][:50]} — no token IDs")
            continue

        print(f"\n  Market: {m['question'][:60]}")
        print(f"  Slug: {m['event_slug']}")
        print(f"  Token: {token_ids[0][:24]}...")

        odds = fetch_market_odds(token_ids[0], fidelity=10)
        print(f"  Odds points: {len(odds)}")
        if odds:
            print(f"  First: {odds[0]}")
            print(f"  Last:  {odds[-1]}")
