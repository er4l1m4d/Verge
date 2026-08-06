"""Backtest Report Generator — Phase 3.3.

Takes results from directional/mispricing backtests and outputs:
  - Printed summary (win rate, avg win/loss, ROI estimate)
  - Saved PNG chart of cumulative P&L
"""
import pandas as pd
import numpy as np
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))


def generate_report(
    results: pd.DataFrame,
    bet_size_pct: float = 1.0,
    output_dir: str = "output",
) -> str:
    """Generate a backtest report from results DataFrame.

    Args:
        results: DataFrame from run_directional_backtest or run_mispricing_backtest
        bet_size_pct: Position size as % of bankroll (default 1%)
        output_dir: Directory to save the chart PNG

    Returns:
        Formatted report string.
    """
    total = len(results)
    if total == 0:
        return "No results to report."

    acted = results[~results["skipped"]].copy()
    skipped = results[results["skipped"]]

    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("BACKTEST REPORT")
    report_lines.append("=" * 60)
    report_lines.append(f"Total hours analyzed:  {total}")
    report_lines.append(f"Hours acted on:        {len(acted)} ({len(acted)/total*100:.0f}%)")
    report_lines.append(f"Hours skipped:         {len(skipped)} ({len(skipped)/total*100:.0f}%)")

    if len(acted) == 0:
        report_lines.append("\nNo signals exceeded the threshold — no trades to evaluate.")
        report_lines.append("Strategy is correctly filtering noise. Consider adjusting thresholds")
        report_lines.append("only if the filter is too aggressive (see Phase 3.4).")
        report_lines.append("=" * 60)
        return "\n".join(report_lines)

    # Win rate
    wins = int(acted["correct"].sum())
    losses = len(acted) - wins
    win_rate = wins / len(acted) * 100

    report_lines.append(f"\nWin rate (acted):      {win_rate:.1f}% ({wins}W / {losses}L out of {len(acted)})")

    # Score stats
    report_lines.append(f"Avg score (acted):     {acted['score'].mean():.3f}")
    report_lines.append(f"Score std (acted):     {acted['score'].std():.3f}")

    # RSI stats
    report_lines.append(f"Avg RSI (acted):       {acted['rsi'].mean():.1f}")

    # Edge stats
    if "edge_pct" in acted.columns:
        report_lines.append(f"Avg edge % (acted):    {acted['edge_pct'].mean():.1f}%")
        fee_eroded = acted["fee_eroded"].sum()
        report_lines.append(f"Fee-eroded signals:    {fee_eroded} / {len(acted)}")

    # ROI estimate (simple: win_rate * avg_win - (1-win_rate) * avg_loss)
    # For Polymarket: payout is roughly 1/price on win, 0 on loss
    # Simplified: assume even money (50¢ odds) for directional test
    report_lines.append(f"\nPosition size:         {bet_size_pct}% of bankroll")
    if win_rate > 0:
        # Simple ROI: each bet wins ~100% (even money) or loses 100%
        roi = (win_rate / 100) * 1.0 - (1 - win_rate / 100) * 1.0
        report_lines.append(f"Estimated ROI (even):  {roi*100:.1f}% per trade")
        report_lines.append(f"Trades per 100 hours:  {len(acted)/total*100:.0f}")
        report_lines.append(f"ROI per 100 hours:     {roi * len(acted)/total * 100:.2f}%")

    report_lines.append("=" * 60)

    # Cumulative P&L chart
    report_lines.append("\nGenerating P&L chart...")

    os.makedirs(output_dir, exist_ok=True)
    chart_path = os.path.join(output_dir, "backtest_pnl.png")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        cum_pnl = []
        pnl = 0
        for _, row in acted.iterrows():
            if row["correct"]:
                pnl += bet_size_pct  # win
            else:
                pnl -= bet_size_pct  # loss
            cum_pnl.append(pnl)

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(range(len(cum_pnl)), cum_pnl, color="#34d399", linewidth=1.5)
        ax.axhline(y=0, color="#ffffff", alpha=0.3, linewidth=0.5)
        ax.set_title("Cumulative P&L — Directional Backtest", color="white", fontsize=13)
        ax.set_xlabel("Trade #", color="white")
        ax.set_ylabel("Cumulative P&L (%)", color="white")
        ax.tick_params(colors="white")
        fig.patch.set_facecolor("#0f0f0f")
        ax.set_facecolor("#0f0f0f")
        ax.spines["bottom"].set_color("#333333")
        ax.spines["left"].set_color("#333333")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        plt.tight_layout()
        plt.savefig(chart_path, dpi=150, facecolor="#0f0f0f")
        plt.close()
        report_lines.append(f"Chart saved: {chart_path}")
    except Exception as e:
        report_lines.append(f"Chart generation failed: {e}")

    report_lines.append("=" * 60)
    return "\n".join(report_lines)


if __name__ == "__main__":
    from backtest import run_directional_backtest

    np.random.seed(42)
    n_hours = 50
    prices_1h = [100000.0]
    for _ in range(n_hours):
        prices_1h.append(prices_1h[-1] + np.random.uniform(-500, 500))

    df_1h = pd.DataFrame({
        "open_time": [i * 3_600_000 for i in range(n_hours)],
        "open": prices_1h[:-1], "close": prices_1h[1:],
        "high": [max(o, c) + 100 for o, c in zip(prices_1h[:-1], prices_1h[1:])],
        "low": [min(o, c) - 100 for o, c in zip(prices_1h[:-1], prices_1h[1:])],
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
    df_5m = pd.DataFrame(rows_5m)

    results = run_directional_backtest(df_1h, df_5m)
    report = generate_report(results, output_dir="output")
    print(report)
