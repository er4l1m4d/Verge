"""Polymarket historical odds fetcher — Phase 1.3."""
import json
import time
import logging

import requests

log = logging.getLogger("verge.polymarket")

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


def get_polymarket_resolution(condition_id: str, market_id: str | None = None) -> dict | None:
    """Query Gamma API for a closed market's official resolution.

    Args:
        condition_id: The on-chain CTF condition ID from the market.
        market_id: The numeric Gamma API market id (preferred for 15m markets).

    Returns:
        {"outcome": "UP"|"DOWN", "closed_time": str, "outcome_prices": list} or None
        if the market isn't resolved yet or the query fails.
    """
    data = {}

    # Strategy 1: Query by numeric market id (works for both 1h and 15m)
    if market_id:
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets/{market_id}", timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning(f"PM resolution: /markets/{market_id} failed: {e}")

    # Strategy 2: Fallback to condition_ids query
    if not data:
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params={"condition_ids": condition_id}, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if isinstance(result, list):
                data = result[0] if result else {}
        except Exception as e:
            log.warning(f"PM resolution: condition_ids query failed for {condition_id[:16]}...: {e}")

    if not data:
        log.warning(f"PM resolution: no market found for cid={condition_id[:16]}... mid={market_id}")
        return None

    if not data.get("closed"):
        log.warning(f"PM resolution: market {condition_id[:16]}... not closed yet")
        return None

    prices_raw = data.get("outcomePrices")
    outcomes_raw = data.get("outcomes")
    if not prices_raw or not outcomes_raw:
        log.warning(f"PM resolution: missing prices/outcomes for {condition_id[:16]}... — keys: {list(data.keys())[:10]}")
        return None
    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    if not prices or not outcomes or len(prices) < 2 or len(outcomes) < 2:
        log.warning(f"PM resolution: short arrays for {condition_id[:16]}... — prices={prices} outcomes={outcomes}")
        return None
    # Winner is the outcome whose price settled to ~1.0
    winner_idx = 0 if float(prices[0]) > 0.9 else 1
    outcome = outcomes[winner_idx].upper()
    log.info(f"PM resolution: {condition_id[:16]}... mid={market_id} -> {outcome}")
    return {
        "outcome": outcome,
        "closed_time": data.get("closedTime"),
        "outcome_prices": prices,
    }


def get_polymarket_live_market(series_slug: str = "btc-up-or-down-15m") -> dict | None:
    """Query Gamma API for the current active market's prices (for diagnostics comparison).

    Filters to only markets that are currently active (start <= now < end)
    to avoid returning stale/closed markets that Gamma still lists as open.

    Returns:
        {"price_to_beat": float, "up_odds": float, "down_odds": float,
         "question": str, "slug": str, "condition_id": str} or None.
    """
    from datetime import datetime, timezone
    now_ms = int(time.time() * 1000)

    try:
        resp = requests.get(f"{GAMMA_BASE}/events", params={
            "limit": 10,
            "series_slug": series_slug,
            "closed": "false",
        }, timeout=10)
        resp.raise_for_status()
        events = resp.json()
        for event in (events if isinstance(events, list) else []):
            for market in event.get("markets", []):
                if market.get("closed"):
                    continue

                # Time-based filter: must be currently active
                event_start = market.get("eventStartTime") or market.get("startDate")
                end_date = market.get("endDate")
                if not event_start or not end_date:
                    continue
                try:
                    start_dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    start_ms = int(start_dt.timestamp() * 1000)
                    end_ms = int(end_dt.timestamp() * 1000)
                except (ValueError, TypeError):
                    continue
                if now_ms < start_ms or now_ms >= end_ms:
                    continue

                # Parse outcomePrices
                prices_raw = market.get("outcomePrices")
                up_odds = down_odds = None
                if prices_raw:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    if prices and len(prices) >= 2:
                        up_odds = float(prices[0])
                        down_odds = float(prices[1])
                # Parse priceToBeat
                metadata = event.get("eventMetadata") or {}
                ptb = metadata.get("priceToBeat")
                price_to_beat = float(ptb) if ptb else None

                # 15m fallback: use get_15m_opening_reference (60s TWAP)
                if price_to_beat is None and "15m" in series_slug:
                    try:
                        from polymarket_fetcher import get_15m_opening_reference
                        ptb, _ = get_15m_opening_reference(start_ms)
                        if ptb:
                            price_to_beat = ptb
                    except Exception:
                        pass

                return {
                    "price_to_beat": price_to_beat,
                    "up_odds": up_odds,
                    "down_odds": down_odds,
                    "question": market.get("question", ""),
                    "slug": event.get("slug", ""),
                    "condition_id": market.get("conditionId"),
                }
    except Exception:
        pass
    return None


def get_15m_opening_reference(window_start_ms: int) -> tuple[float | None, str]:
    """Recover the opening reference price for a 15m window.

    Tries multiple sources in order:
      1. Gamma API eventMetadata.priceToBeat (official, if available)
      2. RTDS 60s TWAP at window start (ring buffer, closest to Polymarket's method)
      3. On-chain Chainlink 60s TWAP at window start (DB)
      4. Binance 1m candle open at window start
      5. Coinbase spot at window start

    For sources 2-3, computes a 60s TWAP centered on window_start_ms
    to match Polymarket's Chainlink TWAP 60s resolution methodology.

    Returns (price, source_label) or (None, "none").
    """
    import time
    import requests
    now_ms = int(time.time() * 1000)

    # 1. Gamma API eventMetadata.priceToBeat (official Polymarket strike)
    try:
        resp = requests.get(f"{GAMMA_BASE}/events", params={
            "limit": 5,
            "series_slug": "btc-up-or-down-15m",
            "closed": "false",
        }, timeout=10)
        resp.raise_for_status()
        events = resp.json()
        for event in (events if isinstance(events, list) else []):
            event_start = event.get("eventStartTime") or event.get("startDate")
            if not event_start:
                continue
            from datetime import datetime, timezone
            try:
                start_dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
                event_start_ms = int(start_dt.timestamp() * 1000)
            except (ValueError, TypeError):
                continue
            if abs(event_start_ms - window_start_ms) > 900_000:
                continue
            metadata = event.get("eventMetadata") or {}
            ptb = metadata.get("priceToBeat")
            if ptb:
                try:
                    return float(ptb), "gamma_price_to_beat"
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    # 2. RTDS 60s TWAP at window start (ring buffer, no DB hit)
    try:
        from polymarket_rtds import get_rtds_ticks
        from engine import compute_twap, PriceSnapshotRow
        ticks = get_rtds_ticks(since_ms=window_start_ms - 65_000)
        if ticks:
            rows = [PriceSnapshotRow(source="rtds_chainlink", symbol="BTCUSD",
                                     price=t["price"], timestamp_ms=t["timestamp_ms"])
                    for t in ticks]
            twap = compute_twap(rows, window_end_ms=window_start_ms, window_seconds=60)
            if twap:
                return twap, "rtds_chainlink_twap_60s"
    except ImportError:
        pass

    # 3. On-chain Chainlink 60s TWAP at window start (DB)
    try:
        import db
        from engine import compute_twap, PriceSnapshotRow
        client = db.get_client()
        ticks = db.get_price_snapshots(
            client, source="chainlink_onchain", symbol="BTC",
            since_ms=window_start_ms - 65_000, limit=100,
        )
        if ticks:
            rows = [PriceSnapshotRow(source="chainlink_onchain", symbol="BTC",
                                     price=float(t["price"]), timestamp_ms=t["timestamp_ms"])
                    for t in ticks]
            twap = compute_twap(rows, window_end_ms=window_start_ms, window_seconds=60)
            if twap:
                return twap, "chainlink_onchain_twap_60s"
    except Exception:
        pass

    # 4. Binance 1m candle open at window start
    try:
        from data_fetcher import get_binance_klines
        df = get_binance_klines(
            symbol="BTCUSDT", interval="1m",
            start_time=window_start_ms, end_time=window_start_ms + 60_000,
            limit=2,
        )
        if df is not None and len(df) > 0:
            return float(df.iloc[0]["open"]), "binance_1m_candle_open"
    except Exception:
        pass

    # 5. Coinbase spot at window start
    try:
        from data_fetcher import get_price_at_time
        price = get_price_at_time(window_start_ms)
        if price:
            return price, "coinbase_spot"
    except Exception:
        pass

    return None, "none"
