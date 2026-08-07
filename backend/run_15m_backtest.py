"""15-Minute Backtest Runner — Phase 11.1.

Fetches Binance 1m klines as a Chainlink stand-in, runs the directional
backtest for 15-minute windows, and prints a report.

CAVEAT: This tests against Binance BTC/USDT 1m data, NOT the actual
Chainlink feed that Polymarket's 15m markets resolve against. Binance
and Chainlink are correlated (~0.01% average deviation for BTC) but not
identical. Results should be treated as a reasonable approximation, not
a guarantee of live performance.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np


def fetch_1m_candles(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch 1m candles. Tries Binance first, falls back to Coinbase."""
    from data_fetcher import get_binance_klines, get_coinbase_candles

    # Try Binance first
    try:
        df = get_binance_klines(symbol="BTCUSDT", interval="1m",
                                start_time=start_ms, end_time=end_ms)
        if df is not None and len(df) > 0:
            print(f"  Using Binance 1m data ({len(df)} candles)")
            return df
    except Exception:
        pass

    # Fallback to Coinbase (60-second granularity)
    try:
        df = get_coinbase_candles(granularity=60, limit=300)
        if df is not None and len(df) > 0:
            print(f"  Using Coinbase 1m data ({len(df)} candles)")
            return df
    except Exception:
        pass

    print("  WARNING: No 1m data available from Binance or Coinbase")
    return pd.DataFrame()


def fetch_15m_candles(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch 15m candles. Tries Binance first, falls back to Coinbase."""
    from data_fetcher import get_binance_klines, get_coinbase_candles

    # Try Binance first
    try:
        df = get_binance_klines(symbol="BTCUSDT", interval="15m",
                                start_time=start_ms, end_time=end_ms)
        if df is not None and len(df) > 0:
            print(f"  Using Binance 15m data ({len(df)} candles)")
            return df
    except Exception:
        pass

    # Fallback to Coinbase (900-second granularity)
    try:
        df = get_coinbase_candles(granularity=900, limit=300)
        if df is not None and len(df) > 0:
            print(f"  Using Coinbase 15m data ({len(df)} candles)")
            return df
    except Exception:
        pass

    print("  WARNING: No 15m data available from Binance or Coinbase")
    return pd.DataFrame()


def _generate_synthetic_data(days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate synthetic 15m and 1m data for local code validation."""
    np.random.seed(42)
    now_ms = int(time.time() * 1000)
    lookback_ms = days * 24 * 60 * 60 * 1000
    start_ms = now_ms - lookback_ms

    # Generate 15m candles
    n_windows = (days * 24 * 60) // 15
    prices_15m = [100000.0]
    for _ in range(n_windows):
        change = np.random.uniform(-200, 200)
        prices_15m.append(prices_15m[-1] + change)

    rows_15m = []
    for i in range(n_windows):
        t = start_ms + i * 900_000
        o = prices_15m[i]
        c = prices_15m[i + 1]
        rows_15m.append({
            "open_time": t,
            "open": o,
            "close": c,
            "high": max(o, c) + abs(np.random.uniform(0, 50)),
            "low": min(o, c) - abs(np.random.uniform(0, 50)),
            "volume": np.random.uniform(100, 1000),
            "close_time": t + 899_999,
        })

    df_15m = pd.DataFrame(rows_15m)

    # Generate 1m candles (15 per 15m window)
    rows_1m = []
    for i, candle in df_15m.iterrows():
        base = candle["open"]
        step = (candle["close"] - candle["open"]) / 15
        for j in range(15):
            t = candle["open_time"] + j * 60_000
            p = base + step * j + np.random.uniform(-10, 10)
            rows_1m.append({
                "open_time": t,
                "open": p,
                "close": p + step + np.random.uniform(-5, 5),
                "high": p + abs(np.random.uniform(0, 15)),
                "low": p - abs(np.random.uniform(0, 15)),
                "volume": np.random.uniform(10, 100),
                "close_time": t + 59_999,
            })

    df_1m = pd.DataFrame(rows_1m)
    return df_15m, df_1m


def run_backtest(days: int = 7):
    """Run 15m directional backtest on recent data.

    Tries Binance/Coinbase first. If both are blocked (e.g. local Windows),
    falls back to synthetic data for validation that the code works correctly.
    """
    from backtest import run_15m_backtest

    now_ms = int(time.time() * 1000)
    lookback_ms = days * 24 * 60 * 60 * 1000
    start_ms = now_ms - lookback_ms

    print(f"Fetching 15m candles ({days} days)...")
    df_15m = fetch_15m_candles(start_ms, now_ms)
    print(f"  Got {len(df_15m)} 15m candles")

    print(f"Fetching 1m candles ({days} days)...")
    df_1m = fetch_1m_candles(start_ms, now_ms)
    print(f"  Got {len(df_1m)} 1m candles")

    # If no real data, generate synthetic data for code validation
    is_synthetic = False
    if len(df_15m) == 0 or len(df_1m) == 0:
        print("\nAPIs unavailable locally — generating synthetic data for code validation")
        print("NOTE: For real results, run on Render: python run_15m_backtest.py 7\n")
        df_15m, df_1m = _generate_synthetic_data(days)
        is_synthetic = True

    if len(df_1m) == 0:
        print("ERROR: No 1m candle data available")
        return

    print(f"\nRunning 15m directional backtest...")
    results = run_15m_backtest(df_15m, df_1m)

    # Report
    total = len(results)
    acted = results[~results["skipped"]]
    skipped = results[results["skipped"]]
    acted_correct = acted["correct"].sum() if len(acted) > 0 else 0

    print(f"\n{'='*60}")
    print(f"15-MINUTE DIRECTIONAL BACKTEST RESULTS")
    print(f"{'='*60}")
    if is_synthetic:
        print(f"Data source:  SYNTHETIC (local code validation only)")
        print(f"Resolution:   Synthetic 15m candles")
    else:
        print(f"Data source:  Binance/Coinbase 1m (Chainlink stand-in)")
        print(f"Resolution:   Binance/Coinbase 15m candles")
    print(f"Period:       {days} days ({total} windows)")
    print(f"{'='*60}")
    print(f"  Windows total:   {total}")
    print(f"  Acted on:        {len(acted)} ({len(acted)/total*100:.0f}%)" if total > 0 else "  Acted on:        0")
    print(f"  Skipped:         {len(skipped)} ({len(skipped)/total*100:.0f}%)" if total > 0 else "  Skipped:         0")
    if len(acted) > 0:
        wr = acted_correct / len(acted) * 100
        print(f"  Win rate:        {wr:.1f}%")
        print(f"  Correct:         {int(acted_correct)}/{len(acted)}")

        # P&L simulation (1% position sizing)
        wins = acted[acted["correct"] == True]
        losses = acted[acted["correct"] == False]
        pnl = len(wins) * 1.0 - len(losses) * 1.0  # simplified
        print(f"  Simulated P&L:   {pnl:+.1f}% (1% sizing)")
    print(f"{'='*60}")
    print(f"CAVEAT: This backtest uses Binance data as a stand-in for")
    print(f"Chainlink. The actual resolution source for Polymarket's 15m")
    print(f"markets is Chainlink Data Streams TWAP 60s. Binance and")
    print(f"Chainlink are correlated but not identical.")
    print(f"{'='*60}")

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), "..", "reports", "15m_backtest.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    results.to_csv(out_path, index=False)
    print(f"\nDetailed results saved to: {out_path}")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    run_backtest(days)
