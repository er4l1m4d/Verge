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
                # Parse priceToBeat from Gamma (comparison only for 15m)
                metadata = event.get("eventMetadata") or {}
                ptb = metadata.get("priceToBeat")
                gamma_price_to_beat = None
                try:
                    if ptb:
                        gamma_price_to_beat = float(ptb)
                        if gamma_price_to_beat <= 0:
                            gamma_price_to_beat = None
                except (TypeError, ValueError):
                    pass

                # 15m: canonical strike is Chainlink 60s TWAP, not Gamma
                price_to_beat = None
                price_to_beat_source = None
                if "15m" in series_slug:
                    try:
                        from polymarket_fetcher import get_15m_opening_reference
                        ptb_val, ptb_source = get_15m_opening_reference(start_ms)
                        if ptb_val:
                            price_to_beat = ptb_val
                            price_to_beat_source = ptb_source
                    except Exception:
                        pass
                else:
                    # 1h: Gamma priceToBeat is canonical
                    price_to_beat = gamma_price_to_beat
                    if price_to_beat is not None:
                        price_to_beat_source = "polymarket_price_to_beat"

                return {
                    "price_to_beat": price_to_beat,
                    "price_to_beat_source": price_to_beat_source,
                    "gamma_price_to_beat": gamma_price_to_beat,
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

    The strike for 15m markets is the Chainlink BTC/USD 60-second TWAP
    at the window start. Polymarket's eventMetadata.priceToBeat is NOT
    populated for 15m markets — only for 1h markets.

    Source priority:
      1. RTDS Chainlink 60s TWAP from in-memory ring buffer (fastest)
      2. RTDS Chainlink 60s TWAP computed from DB snapshots (after restart)
      3. On-chain Chainlink 60s TWAP computed from DB snapshots (fallback)

    Returns (price, source_label) or (None, "none").

    A single tick is NOT a TWAP. If fewer than 2 ticks are available in
    the 60s window, no strike is returned — Polymarket resolves on the
    60-second TWAP stream, not a single observation.
    """
    import time

    # 1. RTDS Chainlink 60s TWAP from in-memory ring buffer (no DB hit)
    try:
        from polymarket_rtds import get_rtds_ticks
        from engine import compute_twap
        from db import PriceSnapshotRow
        ticks = get_rtds_ticks(since_ms=window_start_ms - 65_000)
        if ticks:
            rows = [PriceSnapshotRow(source="rtds_chainlink", symbol="BTCUSD",
                                     price=t["price"], timestamp_ms=t["timestamp_ms"])
                    for t in ticks]
            window_rows = [r for r in rows
                           if window_start_ms - 60_000 <= r.timestamp_ms <= window_start_ms]
            if len(window_rows) >= 2:
                twap = compute_twap(window_rows, window_end_ms=window_start_ms, window_seconds=60)
                if twap:
                    return twap, "rtds_chainlink_twap_60s"
    except ImportError:
        pass

    # 2. RTDS Chainlink 60s TWAP from DB snapshots (after restart, buffer empty)
    try:
        import db
        from engine import compute_twap
        from db import PriceSnapshotRow
        client = db.get_client()
        ticks = db.get_price_snapshots(
            client, source="rtds_chainlink", symbol="BTCUSD",
            since_ms=window_start_ms - 65_000, limit=120,
        )
        window_ticks = [t for t in ticks
                        if window_start_ms - 60_000 <= t["timestamp_ms"] <= window_start_ms]
        if len(window_ticks) >= 2:
            rows = [PriceSnapshotRow(source="rtds_chainlink", symbol="BTCUSD",
                                     price=t["price"], timestamp_ms=t["timestamp_ms"])
                    for t in window_ticks]
            twap = compute_twap(rows, window_end_ms=window_start_ms, window_seconds=60)
            if twap:
                return twap, "rtds_chainlink_twap_60s_db"
    except Exception:
        pass

    # 3. On-chain Chainlink 60s TWAP from DB snapshots
    try:
        import db
        from engine import compute_twap
        from db import PriceSnapshotRow
        client = db.get_client()
        ticks = db.get_price_snapshots(
            client, source="chainlink_onchain", symbol="BTC",
            since_ms=window_start_ms - 65_000, limit=120,
        )
        window_ticks = [t for t in ticks
                        if window_start_ms - 60_000 <= t["timestamp_ms"] <= window_start_ms]
        if len(window_ticks) >= 2:
            rows = [PriceSnapshotRow(source="chainlink_onchain", symbol="BTC",
                                     price=t["price"], timestamp_ms=t["timestamp_ms"])
                    for t in window_ticks]
            twap = compute_twap(rows, window_end_ms=window_start_ms, window_seconds=60)
            if twap:
                return twap, "chainlink_onchain_twap_60s"
    except Exception:
        pass

    return None, "none"


def get_current_15m_reference() -> dict | None:
    """Compute the current 60s Chainlink TWAP from the live RTDS ring buffer.

    This is the "current reference" for 15m markets — the live equivalent
    of the opening strike. It represents where the market's reference
    mechanism currently sits, not the latest instantaneous tick.

    Returns:
        {
            "twap_60s": float,          # live 60s TWAP
            "twap_source": str,         # source label
            "live_spot": float,         # latest raw tick
            "live_spot_source": str,    # source of raw tick
            "samples": int,             # ticks in the 60s window
            "spot_age_ms": int,         # age of latest tick
            "freshness": str,           # LIVE | DELAYED | STALE | INVALID
        } or None
    """
    import time as _time
    now_ms = int(_time.time() * 1000)

    try:
        from polymarket_rtds import get_rtds_ticks
        from engine import compute_twap
        from db import PriceSnapshotRow

        ticks = get_rtds_ticks(since_ms=now_ms - 90_000)
        if not ticks:
            return None

        latest_tick = ticks[-1]
        spot_age_ms = now_ms - latest_tick["timestamp_ms"]

        # Compute live 60s TWAP
        rows = [PriceSnapshotRow(source="rtds_chainlink", symbol="BTCUSD",
                                 price=t["price"], timestamp_ms=t["timestamp_ms"])
                for t in ticks]
        window_rows = [r for r in rows if r.timestamp_ms >= now_ms - 60_000]

        twap = None
        twap_samples = 0
        if len(window_rows) >= 2:
            twap = compute_twap(window_rows, window_end_ms=now_ms, window_seconds=60)
            twap_samples = len(window_rows)

        freshness = classify_freshness(spot_age_ms)

        return {
            "twap_60s": twap,
            "twap_source": "rtds_chainlink_twap_60s" if twap else None,
            "live_spot": latest_tick["price"],
            "live_spot_source": "rtds_chainlink",
            "samples": twap_samples,
            "spot_age_ms": spot_age_ms,
            "freshness": freshness,
        }
    except ImportError:
        pass

    return None


def classify_freshness(age_ms: int) -> str:
    """Classify price data freshness based on age.

    Thresholds:
        < 2s       LIVE
        2-5s       DELAYED
        5-15s      STALE
        > 15s      INVALID
    """
    if age_ms < 2_000:
        return "LIVE"
    elif age_ms < 5_000:
        return "DELAYED"
    elif age_ms < 15_000:
        return "STALE"
    else:
        return "INVALID"
