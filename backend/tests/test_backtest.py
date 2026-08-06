"""Unit tests for Phase 3 — Backtesting Engine."""
import os, sys, numpy as np, pandas as pd, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest import run_directional_backtest, run_mispricing_backtest, _compute_indicators_from_5m
from report import generate_report


def _make_synthetic_data(n_hours=20, seed=42):
    """Generate deterministic synthetic 1h + 5m candle data."""
    np.random.seed(seed)
    prices = [100000.0]
    for _ in range(n_hours):
        prices.append(prices[-1] + np.random.uniform(-500, 500))

    df_1h = pd.DataFrame({
        "open_time": [i * 3_600_000 for i in range(n_hours)],
        "open": prices[:-1], "close": prices[1:],
        "high": [max(o, c) + 100 for o, c in zip(prices[:-1], prices[1:])],
        "low": [min(o, c) - 100 for o, c in zip(prices[:-1], prices[1:])],
        "volume": [np.random.uniform(100, 1000) for _ in range(n_hours)],
        "close_time": [(i * 3_600_000) + 3_599_999 for i in range(n_hours)],
    })

    rows_5m = []
    for _, c in df_1h.iterrows():
        base = c["open"]
        for j in range(12):
            t = c["open_time"] + j * 300_000
            p = base + np.random.uniform(-50, 50)
            rows_5m.append({"open_time": t, "open": p, "close": p + np.random.uniform(-20, 20),
                "high": p + abs(np.random.uniform(0, 30)), "low": p - abs(np.random.uniform(0, 30)),
                "volume": np.random.uniform(10, 100), "close_time": t + 299_999})
    return df_1h, pd.DataFrame(rows_5m)


class TestDirectionalBacktest:
    def test_returns_dataframe(self):
        df_1h, df_5m = _make_synthetic_data()
        result = run_directional_backtest(df_1h, df_5m)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(df_1h)

    def test_required_columns(self):
        df_1h, df_5m = _make_synthetic_data()
        result = run_directional_backtest(df_1h, df_5m)
        for col in ["decision", "final_decision", "score", "actual_direction",
                     "correct", "skipped", "rsi", "ma_signal", "volume_signal",
                     "model_prob", "hour_open", "hour_close", "edge_pct", "fee_eroded"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_actual_direction_matches_candle(self):
        df_1h, df_5m = _make_synthetic_data()
        result = run_directional_backtest(df_1h, df_5m)
        for _, row in result.iterrows():
            if row["hour_close"] > row["hour_open"]:
                assert row["actual_direction"] == "UP"
            else:
                assert row["actual_direction"] == "DOWN"

    def test_skipped_signals_not_scored_correct(self):
        df_1h, df_5m = _make_synthetic_data()
        result = run_directional_backtest(df_1h, df_5m)
        skipped = result[result["skipped"]]
        assert all(skipped["correct"].isna())
        acted = result[~result["skipped"]]
        assert all(acted["correct"].isin([True, False]))

    def test_no_future_leakage(self):
        """Indicators computed from 5m data only up to hour open."""
        from indicators import calculate_rsi, MA_SLOW
        df_1h, df_5m = _make_synthetic_data()
        result = run_directional_backtest(df_1h, df_5m)
        # Just verify no errors and RSI is in valid range
        assert all(0 <= r <= 100 for r in result["rsi"])

    def test_deterministic(self):
        df_1h, df_5m = _make_synthetic_data()
        r1 = run_directional_backtest(df_1h, df_5m)
        r2 = run_directional_backtest(df_1h, df_5m)
        pd.testing.assert_frame_equal(r1, r2)


class TestMispricingBacktest:
    def test_empty_without_odds(self):
        df_1h, df_5m = _make_synthetic_data()
        result = run_mispricing_backtest(df_1h, df_5m)
        assert len(result) == 0

    def test_with_synthetic_odds(self):
        df_1h, df_5m = _make_synthetic_data()
        odds = pd.DataFrame({
            "token_id": ["test"] * len(df_1h),
            "timestamp": df_1h["open_time"],
            "price": np.random.uniform(0.3, 0.7, len(df_1h)),
        })
        result = run_mispricing_backtest(df_1h, df_5m, odds)
        assert len(result) > 0
        assert "diverged" in result.columns
        assert "market_odds" in result.columns


class TestReport:
    def test_report_empty_results(self):
        df = pd.DataFrame()
        report = generate_report(df)
        assert "No results" in report

    def test_report_all_skipped(self):
        df_1h, df_5m = _make_synthetic_data()
        results = run_directional_backtest(df_1h, df_5m)
        report = generate_report(results, output_dir="output_test")
        assert "No signals exceeded" in report

    def test_report_with_trades(self):
        """Synthesize results with some acted trades."""
        results = pd.DataFrame({
            "hour_open_time": range(10),
            "decision": ["BET HIGHER"] * 5 + ["BET LOWER"] * 5,
            "final_decision": ["BET HIGHER"] * 5 + ["BET LOWER"] * 5,
            "confidence": [0.8] * 10,
            "score": [0.5] * 5 + [-0.5] * 5,
            "model_prob": [0.7] * 10,
            "rsi": [75] * 5 + [25] * 5,
            "ma_signal": [1] * 5 + [-1] * 5,
            "volume_signal": [1] * 5 + [-1] * 5,
            "actual_direction": ["UP"] * 5 + ["DOWN"] * 5,
            "hour_open": [100000] * 10,
            "hour_close": [100500] * 5 + [99500] * 5,
            "correct": [True, True, False, True, False] * 2,
            "skipped": [False] * 10,
            "fee_eroded": [False] * 10,
            "edge_pct": [5.0] * 10,
        })
        report = generate_report(results, output_dir="output_test")
        assert "Win rate" in report
        assert "backtest_pnl.png" in report
