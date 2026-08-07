"""Binance klines fetcher with parquet caching — Phase 1.1 + 1.2."""
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("verge.data")

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]


def get_binance_klines(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    limit: int = 1000,
) -> pd.DataFrame:
    """Fetch klines from Binance REST API, paginating as needed.

    Args:
        symbol: e.g. "BTCUSDT"
        interval: e.g. "1h", "5m"
        start_time: epoch ms — inclusive
        end_time: epoch ms — inclusive
        limit: max rows per request (Binance caps at 1000)

    Returns:
        DataFrame with columns: open_time, open, high, low, close, volume,
        close_time — all numeric types.
    """
    all_rows = []
    current_start = start_time

    while current_start <= end_time:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_time,
            "limit": limit,
        }
        try:
            resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"Cannot reach Binance API ({BINANCE_KLINES_URL}). "
                "Check network/DNS. On Windows, try: nslookup api.binance.com 8.8.8.8"
            ) from e

        if not data:
            break

        all_rows.extend(data)

        # Move start to after the last candle's close time
        last_close_time = data[-1][6]
        current_start = last_close_time + 1

        # Respect rate limits — Binance allows 1200 req/min
        if len(data) == limit:
            time.sleep(0.1)

    if not all_rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(all_rows, columns=COLUMNS + [
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    df = df[COLUMNS].copy()

    # Convert types
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = df["open_time"].astype(int)
    df["close_time"] = df["close_time"].astype(int)

    # Drop exact duplicates (can happen at boundary)
    df = df.drop_duplicates(subset="open_time").reset_index(drop=True)

    return df


def epoch_ms_to_str(ts_ms: int) -> str:
    """Convert epoch milliseconds to ISO string for logging."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def _cache_path(cache_dir: str, symbol: str, interval: str) -> Path:
    """Build parquet cache file path."""
    return Path(cache_dir) / f"{symbol.lower()}_{interval}.parquet"


def load_cache(cache_dir: str, symbol: str, interval: str) -> pd.DataFrame | None:
    """Load cached klines from parquet, or return None if not cached."""
    path = _cache_path(cache_dir, symbol, interval)
    if path.exists():
        return pd.read_parquet(path)
    return None


def save_cache(df: pd.DataFrame, cache_dir: str, symbol: str, interval: str) -> None:
    """Save klines to parquet cache."""
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, symbol, interval)
    df.to_parquet(path, index=False)


def fetch_with_cache(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    cache_dir: str = "data",
) -> pd.DataFrame:
    """Fetch klines with incremental caching.

    On first run: fetches full range, saves to parquet.
    On subsequent runs: loads cache, fetches only the missing tail,
    appends, and saves.
    """
    cached = load_cache(cache_dir, symbol, interval)

    if cached is not None and len(cached) > 0:
        # Fetch from last cached candle onwards
        last_cached_time = int(cached["close_time"].max())
        fetch_start = last_cached_time + 1

        if fetch_start > end_time:
            # Cache is already up to date
            return cached[cached["open_time"] >= start_time].reset_index(drop=True)

        new_data = get_binance_klines(symbol, interval, fetch_start, end_time)
        if len(new_data) > 0:
            combined = pd.concat([cached, new_data], ignore_index=True)
            combined = (
                combined.drop_duplicates(subset="open_time")
                .sort_values("open_time")
                .reset_index(drop=True)
            )
        else:
            combined = cached

        save_cache(combined, cache_dir, symbol, interval)
        return combined[combined["open_time"] >= start_time].reset_index(drop=True)
    else:
        # No cache — full fetch
        df = get_binance_klines(symbol, interval, start_time, end_time)
        if len(df) > 0:
            save_cache(df, cache_dir, symbol, interval)
        return df


def get_coingecko_ohlc(
    days: int = 1,
) -> pd.DataFrame:
    """Fetch BTC OHLC candles from CoinGecko (fallback when Binance is blocked).

    Returns DataFrame with same columns as get_binance_klines.
    CoinGecko OHLC intervals: 30m candles (close enough for indicator computation).
    """
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
    params = {"vs_currency": "usd", "days": days}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Warning: CoinGecko request failed: {e}")
        return pd.DataFrame(columns=COLUMNS)

    if not data:
        return pd.DataFrame(columns=COLUMNS)

    rows = []
    for candle in data:
        ts, open_p, high, low, close = candle
        rows.append({
            "open_time": int(ts),
            "open": float(open_p),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": 0.0,  # CoinGecko OHLC doesn't include volume
            "close_time": int(ts) + 1_799_999,  # ~30 min
        })

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    return df


def get_spot_price() -> float | None:
    """Fetch current BTC-USD spot price. Fast path first, fallback to CoinGecko."""
    # Fast path: Coinbase ticker (no key, faster than CoinGecko)
    try:
        r = requests.get(
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            headers=HEADERS,
            timeout=5,
        )
        r.raise_for_status()
        return float(r.json()["data"]["amount"])
    except Exception:
        pass
    # Fallback: CoinGecko
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            headers=HEADERS,
            timeout=5,
        )
        r.raise_for_status()
        return float(r.json()["bitcoin"]["usd"])
    except Exception as e:
        print(f"[data_fetcher] spot price failed: {e}")
        return None


def get_coinbase_candles(
    granularity: int = 300,
    limit: int = 300,
) -> pd.DataFrame:
    """Fetch BTC-USD candles from Coinbase Exchange API (includes volume).

    Args:
        granularity: candle width in seconds (300 = 5m)
        limit: max candles (Coinbase caps at 300)

    Returns:
        DataFrame with same columns as get_binance_klines.
    """
    params = {"granularity": granularity, "limit": limit}

    try:
        resp = requests.get(COINBASE_CANDLES_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"Coinbase request failed: {e}")
        return pd.DataFrame(columns=COLUMNS)

    if not data:
        return pd.DataFrame(columns=COLUMNS)

    # Coinbase returns: [[time, low, high, open, close, volume], ...]
    rows = []
    for candle in data:
        ts, low, high, open_p, close_p, volume = candle
        rows.append({
            "open_time": int(ts) * 1000,  # convert to ms
            "open": float(open_p),
            "high": float(high),
            "low": float(low),
            "close": float(close_p),
            "volume": float(volume),
            "close_time": (int(ts) + granularity) * 1000 - 1,
        })

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    return df


def get_price_with_fallback(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
) -> pd.DataFrame:
    """Try Binance first, then Coinbase (has volume), then CoinGecko."""
    try:
        df = get_binance_klines(symbol, interval, start_time, end_time)
        if len(df) > 0:
            log.info("Using Binance data")
            return df
    except Exception:
        pass

    # Binance failed — try Coinbase (has per-candle volume)
    try:
        df = get_coinbase_candles(granularity=300, limit=300)
        if len(df) > 0:
            log.info("Using Coinbase data (with volume)")
            return df
    except Exception:
        pass

    # Coinbase failed — try CoinGecko (no volume)
    log.warning("Binance/Coinbase unavailable, falling back to CoinGecko (no volume)")
    return get_coingecko_ohlc(days=1)


if __name__ == "__main__":
    # Quick sanity check: fetch last 5 days with caching
    now = int(time.time() * 1000)
    five_days_ago = now - (5 * 24 * 60 * 60 * 1000)

    print("Fetching 5 days of 1h klines (with cache)...")
    t0 = time.time()
    df = fetch_with_cache("BTCUSDT", "1h", five_days_ago, now, cache_dir="data")
    elapsed = time.time() - t0
    print(f"  Rows: {len(df)} | Time: {elapsed:.2f}s")
    print(f"  Range: {epoch_ms_to_str(df['open_time'].iloc[0])} → {epoch_ms_to_str(df['open_time'].iloc[-1])}")

    print("\nSecond run (should read from cache)...")
    t0 = time.time()
    df2 = fetch_with_cache("BTCUSDT", "1h", five_days_ago, now, cache_dir="data")
    elapsed = time.time() - t0
    print(f"  Rows: {len(df2)} | Time: {elapsed:.4f}s (should be <1s)")

    print("\nFetching 5 days of 5m klines (with cache)...")
    t0 = time.time()
    df5 = fetch_with_cache("BTCUSDT", "5m", five_days_ago, now, cache_dir="data")
    elapsed = time.time() - t0
    print(f"  Rows: {len(df5)} | Time: {elapsed:.2f}s")
