"""Chainlink Price Source — Phase 3.

Reads BTC/USD price from Chainlink's on-chain Data Feed on Polygon Mainnet,
and builds 1-minute OHLC bars for indicator computation in the 15m signal path.

---

APPROXIMATION ACCEPTED (Phase 3.1):

The actual resolution source for Polymarket's 15-minute BTC markets is
Chainlink's Data Streams TWAP 60s product
(https://data.chain.link/streams/btc-usd-twcap-60s-streams). This module
reads Chainlink's older on-chain Data Feed product instead — same oracle
network, same 0.1% deviation threshold, but not guaranteed tick-identical
to the literal resolution feed.

Why this gap exists: Data Streams is a low-latency product that typically
requires a paid subscription to query directly. The on-chain Data Feed is
freely readable via any Polygon RPC node with no API key. Both products
share the same oracle network (17 operators for this feed) and the same
deviation threshold (0.1%), so prices should be very close — but there
will be occasional small differences.

This is the same category of gap as the CoinGecko-vs-Binance mismatch
this project already caught and documented. The goal is not to eliminate
it (there is no free way to), but to make sure nobody forgets it's there.

Contract: 0xc907E116054Ad103354f2D350FD2514433D57F6f (Polygon Mainnet)
Feed: BTC/USD-RefPrice-DF-Matic-001
Decimals: 8 (standard for BTC/USD feeds)
---
"""

import os
import time
import logging
from typing import Optional

import pandas as pd

log = logging.getLogger("verge.chainlink")

# ---------------------------------------------------------------------------
# Chainlink on-chain Data Feed — Polygon Mainnet BTC/USD
# ---------------------------------------------------------------------------

FEED_ADDRESS = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
RPC_URL = os.environ.get(
    "POLYGON_RPC_URL",
    "https://polygon-bor-rpc.publicnode.com",
)
FEED_DECIMALS = 8  # read from contract, but known for BTC/USD

# AggregatorV3Interface ABI — only the functions we need
AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def get_chainlink_price() -> Optional[float]:
    """Read the current BTC/USD price from the Chainlink on-chain Data Feed.

    Returns the price as a float (e.g. 65000.0), or None on failure.
    The feed returns prices with 8 decimal places — this function divides
    by 10^8 to return a human-readable price.
    """
    try:
        from web3 import Web3
    except ImportError:
        log.warning("web3 not installed — cannot read Chainlink feed")
        return None

    try:
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        if not w3.is_connected():
            log.warning(f"Cannot connect to Polygon RPC: {RPC_URL}")
            return None

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(FEED_ADDRESS),
            abi=AGGREGATOR_ABI,
        )

        # Read latest round data
        round_data = contract.functions.latestRoundData().call()
        # round_data = (roundId, answer, startedAt, updatedAt, answeredInRound)
        raw_price = round_data[1]
        updated_at = round_data[3]

        # Check staleness — if data is older than 1 hour, warn
        age_seconds = int(time.time()) - updated_at
        if age_seconds > 3600:
            log.warning(
                f"Chainlink feed is stale: {age_seconds}s old "
                f"(updated_at={updated_at})"
            )

        # Convert from 8-decimal integer to float
        price = raw_price / (10 ** FEED_DECIMALS)

        if price <= 0:
            log.warning(f"Chainlink returned non-positive price: {price}")
            return None

        return price

    except Exception as e:
        log.warning(f"Chainlink price read failed: {e}")
        return None


def resample_to_bars(
    ticks: list[dict],
    interval_ms: int = 60_000,
) -> pd.DataFrame:
    """Resample raw price ticks into OHLC bars.

    Args:
        ticks: list of {"timestamp_ms": int, "price": float}
        interval_ms: bar width in milliseconds (default 60000 = 1 minute)

    Returns:
        DataFrame with columns: open_time, open, high, low, close, volume,
        close_time. Volume is set to 0 (Chainlink has no volume data).
    """
    if not ticks:
        return pd.DataFrame(columns=[
            "open_time", "open", "high", "low", "close", "volume", "close_time"
        ])

    df = pd.DataFrame(ticks)
    df["timestamp_ms"] = pd.to_numeric(df["timestamp_ms"])
    df["price"] = pd.to_numeric(df["price"])

    # Assign each tick to a bar based on its timestamp
    df["bar_start"] = (df["timestamp_ms"] // interval_ms) * interval_ms

    bars = []
    for bar_start, group in df.groupby("bar_start"):
        bars.append({
            "open_time": int(bar_start),
            "open": float(group.iloc[0]["price"]),
            "high": float(group["price"].max()),
            "low": float(group["price"].min()),
            "close": float(group.iloc[-1]["price"]),
            "volume": 0.0,  # Chainlink has no volume
            "close_time": int(bar_start) + interval_ms - 1,
        })

    return pd.DataFrame(bars)


def bootstrap_from_binance(
    interval_ms: int = 60_000,
    lookback_candles: int = 20,
) -> pd.DataFrame:
    """Fetch recent 1-minute Binance candles as bootstrap data.

    This eliminates the warm-up period for 15m indicators by seeding
    the bar-builder with historical data. The price source for the 15m
    market is Chainlink, but Binance 1m candles are a close approximation
    for indicator computation (same BTC, ~0.01% difference on average).

    Volume is included from Binance (real market activity), not proxied.
    """
    from data_fetcher import get_binance_klines

    now_ms = int(time.time() * 1000)
    # Fetch a bit more than needed to ensure we have enough after alignment
    start_ms = now_ms - (lookback_candles + 10) * interval_ms

    try:
        df = get_binance_klines(
            symbol="BTCUSDT",
            interval="1m",
            start_time=start_ms,
            end_time=now_ms,
        )
        if df is not None and len(df) > 0:
            log.info(f"Bootstrap: fetched {len(df)} 1m candles from Binance")
            return df
        else:
            log.warning("Bootstrap: Binance returned no data")
            return pd.DataFrame(columns=[
                "open_time", "open", "high", "low", "close", "volume", "close_time"
            ])
    except Exception as e:
        log.warning(f"Bootstrap from Binance failed: {e}")
        return pd.DataFrame(columns=[
            "open_time", "open", "high", "low", "close", "volume", "close_time"
        ])


def get_chainlink_bars(
    cached_ticks: list[dict],
    interval_ms: int = 60_000,
    min_bars: int = 20,
) -> pd.DataFrame:
    """Get 1-minute OHLC bars for indicator computation.

    Strategy:
    1. If we have enough cached ticks, resample them into bars
    2. If not enough, bootstrap from Binance 1m candles
    3. If both fail, return empty DataFrame

    Args:
        cached_ticks: accumulated price ticks from price_snapshots table
        interval_ms: bar width (default 60000 = 1 minute)
        min_bars: minimum bars needed for valid indicators

    Returns:
        DataFrame with OHLCV columns, or empty DataFrame if insufficient data
    """
    # Try resampling cached ticks first
    if cached_ticks and len(cached_ticks) >= min_bars:
        bars = resample_to_bars(cached_ticks, interval_ms)
        if len(bars) >= min_bars:
            log.info(f"Using {len(bars)} bars from cached Chainlink ticks")
            return bars
        else:
            log.info(
                f"Cached ticks produced {len(bars)} bars "
                f"(need {min_bars}), trying Binance bootstrap"
            )

    # Bootstrap from Binance
    log.info("Bootstrapping 1m bars from Binance")
    bootstrap_df = bootstrap_from_binance(interval_ms, min_bars)
    if len(bootstrap_df) >= min_bars:
        return bootstrap_df

    log.warning(
        f"Insufficient data: got {len(bootstrap_df)} bars, "
        f"need {min_bars}"
    )
    return pd.DataFrame(columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time"
    ])
