"""Data alignment — Phase 1.4.

Joins Polymarket odds timelines with Binance 1h/5m candles to produce
one row per historical hourly market, ready for backtesting.
"""
import json
from datetime import datetime, timezone

import pandas as pd

from data_fetcher import get_binance_klines
from polymarket_fetcher import search_hourly_btc_markets, fetch_market_odds


def parse_end_date(end_date: str) -> datetime:
    """Parse ISO date string to UTC datetime."""
    return datetime.fromisoformat(end_date.replace("Z", "+00:00"))


def align_market(
    market: dict,
    odds_fidelity: int = 5,
) -> dict | None:
    """Align one Polymarket market with Binance candle data.

    Returns a dict with:
        event_slug, market_id, condition_id, question,
        hour_open, hour_close, actual_direction,
        odds_at_start, odds_at_mid, odds_at_end,
        odds_timeline (list of {timestamp, price}),
        five_min_prices (list of float — the 5m closes for indicator calc),
        error (str if something went wrong, else None).
    """
    result = {
        "event_slug": market.get("event_slug"),
        "market_id": market.get("market_id"),
        "condition_id": market.get("condition_id"),
        "question": market.get("question"),
        "end_date": market.get("end_date"),
        "hour_open": None,
        "hour_close": None,
        "actual_direction": None,
        "odds_at_start": None,
        "odds_at_mid": None,
        "odds_at_end": None,
        "odds_timeline": [],
        "five_min_prices": [],
        "error": None,
    }

    end_date_str = market.get("end_date")
    if not end_date_str:
        result["error"] = "no end_date"
        return result

    try:
        end_dt = parse_end_date(end_date_str)
    except Exception as e:
        result["error"] = f"date parse error: {e}"
        return result

    # The market hour: from (end_dt - 1h) to end_dt
    hour_start_dt = end_dt - pd.Timedelta(hours=1)
    hour_start_ms = int(hour_start_dt.timestamp() * 1000)
    hour_end_ms = int(end_dt.timestamp() * 1000)

    # Fetch 1h candle for this hour
    try:
        df_1h = get_binance_klines("BTCUSDT", "1h", hour_start_ms, hour_end_ms)
    except Exception as e:
        result["error"] = f"Binance 1h fetch error: {e}"
        return result

    if len(df_1h) == 0:
        result["error"] = "no 1h candle found"
        return result

    row = df_1h.iloc[0]
    result["hour_open"] = float(row["open"])
    result["hour_close"] = float(row["close"])
    result["actual_direction"] = "UP" if result["hour_close"] > result["hour_open"] else "DOWN"

    # Fetch 5m candles for the hour (preceding 15 min + the hour itself for indicators)
    indicator_start_ms = hour_start_ms - (15 * 60 * 1000)  # 15 min lookback for RSI-14
    try:
        df_5m = get_binance_klines("BTCUSDT", "5m", indicator_start_ms, hour_end_ms)
    except Exception as e:
        result["error"] = f"Binance 5m fetch error: {e}"
        return result

    if len(df_5m) > 0:
        result["five_min_prices"] = df_5m["close"].tolist()

    # Fetch odds from CLOB
    token_ids = market.get("clob_token_ids", [])
    if token_ids:
        odds = fetch_market_odds(token_ids[0], fidelity=odds_fidelity)
        result["odds_timeline"] = odds

        if odds:
            result["odds_at_start"] = odds[0].get("price")
            result["odds_at_end"] = odds[-1].get("price")
            mid_idx = len(odds) // 2
            result["odds_at_mid"] = odds[mid_idx].get("price")
        else:
            result["error"] = "resolved market — no odds history available"
    else:
        result["error"] = "no token IDs"

    return result


def align_all_markets(
    series_slug: str = "btc-up-or-down-hourly",
    limit: int = 20,
    odds_fidelity: int = 5,
) -> pd.DataFrame:
    """Fetch and align multiple historical markets.

    Returns a DataFrame with one row per market, ready for backtesting.
    """
    markets = search_hourly_btc_markets(series_slug=series_slug, limit=limit)
    print(f"Found {len(markets)} markets to align")

    rows = []
    for i, m in enumerate(markets):
        print(f"  [{i+1}/{len(markets)}] {m['question'][:50]}...", end=" ")
        aligned = align_market(m, odds_fidelity=odds_fidelity)
        if aligned:
            rows.append(aligned)
            status = "OK" if not aligned["error"] else f"warn: {aligned['error']}"
            print(status)
        else:
            print("SKIP")

    df = pd.DataFrame(rows)
    return df


if __name__ == "__main__":
    print("Aligning 5 hourly BTC markets...")
    df = align_all_markets(limit=5)
    print(f"\nResult: {len(df)} rows")
    print(df[["event_slug", "hour_open", "hour_close", "actual_direction", "odds_at_start", "error"]].to_string())
