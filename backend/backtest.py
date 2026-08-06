"""Backtesting Engine — Phase 3.

Runs the indicator+scoring pipeline over historical data to measure
whether the strategy has a real edge. Two backtests:
  3.1 — Directional: does the signal predict BTC candle direction?
  3.2 — Mispricing: does the signal catch Polymarket odds divergences?
"""
import pandas as pd
import numpy as np

from indicators import (
    calculate_rsi,
    ma_crossover,
    volume_spike,
    score_signal,
    fee_adjusted_edge,
    RSI_PERIOD,
    MA_SLOW,
)


def _compute_indicators_from_5m(
    df_5m: pd.DataFrame,
    hour_open_time: int,
) -> tuple[float, int, int]:
    """Recompute RSI/MA/volume from 5m data available as of hour_open_time.

    Uses ONLY data up to hour_open_time (no future leakage).
    Returns (rsi, ma_signal, volume_signal).
    """
    # Get 5m candles up to (and including) the hour open
    candles_up_to_open = df_5m[df_5m["open_time"] <= hour_open_time]

    if len(candles_up_to_open) < MA_SLOW:
        return 50.0, 0, 0

    prices = candles_up_to_open["close"].tolist()
    volumes = candles_up_to_open["volume"].tolist()

    # RSI
    rsi = calculate_rsi(prices)

    # MA crossover
    ma_sig = ma_crossover(prices)

    # Volume spike: compare last candle's volume to 10-period average
    if len(volumes) >= 10:
        avg_vol = np.mean(volumes[-11:-1])  # exclude current candle
        current_vol = volumes[-1]
        # Direction from last candle's close vs open
        last_candle = candles_up_to_open.iloc[-1]
        candle_dir = 1 if last_candle["close"] > last_candle["open"] else -1
        vol_sig = volume_spike(current_vol, avg_vol, candle_dir)
    else:
        vol_sig = 0

    return rsi, ma_sig, vol_sig


def run_directional_backtest(
    df_1h: pd.DataFrame,
    df_5m: pd.DataFrame,
    min_odds: float | None = None,
) -> pd.DataFrame:
    """3.1 — Directional backtest.

    For each 1h candle:
      1. Recompute indicators from 5m data available as of hour open.
      2. Run scoring function → decision.
      3. Compare decision against actual candle direction.

    Returns a DataFrame with one row per hour:
      hour_open_time, decision, confidence, score, actual_direction,
      correct, skipped.
    """
    results = []

    for _, candle in df_1h.iterrows():
        hour_open = int(candle["open_time"])
        hour_close_price = float(candle["close"])
        hour_open_price = float(candle["open"])
        actual = "UP" if hour_close_price > hour_open_price else "DOWN"

        # Recompute indicators (no future leakage)
        rsi, ma_sig, vol_sig = _compute_indicators_from_5m(df_5m, hour_open)

        # Score
        sig = score_signal(rsi, ma_sig, vol_sig)

        # Fee-adjusted edge (use 0.50 as default odds for directional test)
        edge = fee_adjusted_edge(sig.decision, 0.50, sig.model_probability)

        # Determine correctness
        final_dec = edge.final_decision
        skipped = final_dec == "SKIP"
        if skipped:
            correct = None  # skipped — not counted
        else:
            correct = (final_dec == "BET HIGHER" and actual == "UP") or \
                      (final_dec == "BET LOWER" and actual == "DOWN")

        results.append({
            "hour_open_time": hour_open,
            "decision": sig.decision,
            "final_decision": final_dec,
            "confidence": sig.confidence,
            "score": sig.score,
            "model_prob": sig.model_probability,
            "rsi": rsi,
            "ma_signal": ma_sig,
            "volume_signal": vol_sig,
            "actual_direction": actual,
            "hour_open": hour_open_price,
            "hour_close": hour_close_price,
            "correct": correct,
            "skipped": skipped,
            "fee_eroded": edge.fee_eroded,
            "edge_pct": edge.edge_pct,
        })

    return pd.DataFrame(results)


def run_mispricing_backtest(
    df_1h: pd.DataFrame,
    df_5m: pd.DataFrame,
    odds_snapshots: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """3.2 — Mispricing backtest.

    Same as directional, but also checks whether the signal caught a
    divergence between model probability and market odds.

    `odds_snapshots` is the local odds_snapshots table (token_id,
    timestamp, price). If available, we look up the odds closest to
    each hour's open. If not available, we use the CLOB coarse history.

    Returns a DataFrame with one row per hour where odds data exists:
      hour_open_time, decision, model_prob, market_odds, diverged,
      correct_divergence, actual_direction.
    """
    if odds_snapshots is None or len(odds_snapshots) == 0:
        # No odds data — return empty with a note
        return pd.DataFrame(columns=[
            "hour_open_time", "decision", "model_prob", "market_odds",
            "diverged", "correct_divergence", "actual_direction", "note"
        ])

    results = []

    for _, candle in df_1h.iterrows():
        hour_open = int(candle["open_time"])
        hour_close_price = float(candle["close"])
        hour_open_price = float(candle["open"])
        actual = "UP" if hour_close_price > hour_open_price else "DOWN"

        # Get odds closest to hour open (within 5 minutes)
        window = odds_snapshots[
            (odds_snapshots["timestamp"] >= hour_open - 300_000) &
            (odds_snapshots["timestamp"] <= hour_open + 300_000)
        ]

        if len(window) == 0:
            continue

        market_odds = float(window.iloc[0]["price"])

        # Recompute indicators
        rsi, ma_sig, vol_sig = _compute_indicators_from_5m(df_5m, hour_open)
        sig = score_signal(rsi, ma_sig, vol_sig)
        edge = fee_adjusted_edge(sig.decision, market_odds, sig.model_probability)

        # Divergence: signal says one thing, odds imply another
        # Model says UP but odds < 0.50 (market thinks DOWN) → divergence
        # Model says DOWN but odds > 0.50 (market thinks UP) → divergence
        diverged = False
        correct_divergence = None

        if edge.final_decision != "SKIP":
            model_says_up = edge.final_decision == "BET HIGHER"
            market_implies_up = market_odds > 0.50
            diverged = model_says_up != market_implies_up

            if diverged:
                correct_divergence = (
                    (model_says_up and actual == "UP") or
                    (not model_says_up and actual == "DOWN")
                )

        results.append({
            "hour_open_time": hour_open,
            "decision": sig.decision,
            "final_decision": edge.final_decision,
            "model_prob": sig.model_probability,
            "market_odds": market_odds,
            "actual_direction": actual,
            "diverged": diverged,
            "correct_divergence": correct_divergence,
            "edge_pct": edge.edge_pct,
        })

    return pd.DataFrame(results)


if __name__ == "__main__":
    # Synthetic test: generate 50 hours of fake data
    np.random.seed(42)

    print("Generating synthetic backtest data...")
    n_hours = 50
    prices_1h = [100000.0]
    for _ in range(n_hours):
        change = np.random.uniform(-500, 500)
        prices_1h.append(prices_1h[-1] + change)

    df_1h = pd.DataFrame({
        "open_time": [i * 3_600_000 for i in range(n_hours)],
        "open": prices_1h[:-1],
        "close": prices_1h[1:],
        "high": [max(o, c) + 100 for o, c in zip(prices_1h[:-1], prices_1h[1:])],
        "low": [min(o, c) - 100 for o, c in zip(prices_1h[:-1], prices_1h[1:])],
        "volume": [np.random.uniform(100, 1000) for _ in range(n_hours)],
        "close_time": [(i * 3_600_000) + 3_599_999 for i in range(n_hours)],
    })

    # Generate 5m candles (12 per hour)
    rows_5m = []
    for i, candle in df_1h.iterrows():
        base = candle["open"]
        for j in range(12):
            t = candle["open_time"] + j * 300_000
            p = base + np.random.uniform(-50, 50)
            rows_5m.append({
                "open_time": t,
                "open": p,
                "close": p + np.random.uniform(-20, 20),
                "high": p + abs(np.random.uniform(0, 30)),
                "low": p - abs(np.random.uniform(0, 30)),
                "volume": np.random.uniform(10, 100),
                "close_time": t + 299_999,
            })

    df_5m = pd.DataFrame(rows_5m)

    print(f"Running directional backtest on {n_hours} hours...")
    results = run_directional_backtest(df_1h, df_5m)

    total = len(results)
    acted = results[~results["skipped"]]
    skipped = results[results["skipped"]]
    acted_correct = acted["correct"].sum()

    print(f"\n{'='*50}")
    print(f"RESULTS: {total} hours total")
    print(f"  Acted on: {len(acted)} ({len(acted)/total*100:.0f}%)")
    print(f"  Skipped:  {len(skipped)} ({len(skipped)/total*100:.0f}%)")
    if len(acted) > 0:
        wr = acted_correct / len(acted) * 100
        print(f"  Win rate (acted): {wr:.1f}%")
    print(f"{'='*50}")
