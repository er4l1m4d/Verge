"""Phase 2.6 — Unit test suite for indicators engine."""
import sys
import os

# Add backend/ to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from indicators import (
    calculate_rsi,
    ma_crossover,
    volume_spike,
    score_signal,
    fee_adjusted_edge,
    RSI_PERIOD,
)

# ---------------------------------------------------------------------------
# 2.1 — RSI tests
# ---------------------------------------------------------------------------


class TestRSI:
    def test_all_gains(self):
        """Monotonically rising prices → RSI should be 100."""
        prices = [100 + i for i in range(20)]
        assert calculate_rsi(prices) == 100.0

    def test_all_losses(self):
        """Monotonically falling prices → RSI should be 0."""
        prices = [200 - i for i in range(20)]
        assert calculate_rsi(prices) == 0.0

    def test_neutral(self):
        """Flat prices → RSI should be ~50."""
        prices = [100.0] * 20
        assert calculate_rsi(prices) == 50.0

    def test_insufficient_data(self):
        """Fewer than period+1 points → returns 50 (neutral)."""
        prices = [100.0] * 10
        assert calculate_rsi(prices) == 50.0

    def test_empty(self):
        """Empty list → returns 50 (neutral)."""
        assert calculate_rsi([]) == 50.0

    def test_overbought(self):
        """Strong uptrend → RSI > 70."""
        prices = [100 + i * 2 for i in range(20)]
        assert calculate_rsi(prices) > 70

    def test_oversold(self):
        """Strong downtrend → RSI < 30."""
        prices = [200 - i * 2 for i in range(20)]
        assert calculate_rsi(prices) < 30


# ---------------------------------------------------------------------------
# 2.2 — MA crossover tests
# ---------------------------------------------------------------------------


class TestMACrossover:
    def test_uptrend(self):
        """Rising prices → fast MA > slow MA → +1."""
        prices = [100 + i for i in range(20)]
        assert ma_crossover(prices) == 1

    def test_downtrend(self):
        """Falling prices → fast MA < slow MA → -1."""
        prices = [200 - i for i in range(20)]
        assert ma_crossover(prices) == -1

    def test_equal(self):
        """Flat prices → MAs equal → 0."""
        prices = [100.0] * 20
        assert ma_crossover(prices) == 0

    def test_insufficient_data(self):
        """Fewer than slow period → returns 0."""
        prices = [100.0] * 10
        assert ma_crossover(prices) == 0


# ---------------------------------------------------------------------------
# 2.3 — Volume spike tests
# ---------------------------------------------------------------------------


class TestVolumeSpike:
    def test_spike_up(self):
        """Volume ≥3x avg, up candle → +1."""
        assert volume_spike(350, 100, 1) == 1

    def test_spike_down(self):
        """Volume ≥3x avg, down candle → -1."""
        assert volume_spike(350, 100, -1) == -1

    def test_no_spike(self):
        """Volume <3x avg → 0."""
        assert volume_spike(200, 100, 1) == 0

    def test_boundary(self):
        """Volume exactly 3x → spike (≥3x qualifies)."""
        assert volume_spike(300, 100, 1) == 1

    def test_zero_avg(self):
        """Zero average volume → 0 (avoid division by zero)."""
        assert volume_spike(100, 0, 1) == 0


# ---------------------------------------------------------------------------
# 2.4 — Score signal tests
# ---------------------------------------------------------------------------


class TestScoreSignal:
    def test_prd_example(self):
        """PRD worked example: RSI overbought, vol up, MA up → +0.20 → SKIP."""
        r = score_signal(75.0, 1, 1)
        assert abs(r.score - 0.20) < 0.01
        assert r.decision == "SKIP"

    def test_strong_buy(self):
        """RSI oversold + vol up + MA up → strong positive → BET HIGHER."""
        r = score_signal(25.0, 1, 1)
        assert r.decision == "BET HIGHER"
        assert r.confidence == "High"

    def test_strong_sell(self):
        """RSI overbought + vol down + MA down → strong negative → BET LOWER."""
        r = score_signal(75.0, -1, -1)
        assert r.decision == "BET LOWER"
        assert r.confidence == "High"

    def test_weak_buy(self):
        """RSI oversold + no volume + MA neutral → low positive → BET HIGHER Low."""
        r = score_signal(25.0, 0, 0)
        assert r.decision == "BET HIGHER"
        assert r.confidence == "Low"

    def test_neutral(self):
        """RSI 50 + no volume + MA neutral → SKIP."""
        r = score_signal(50.0, 0, 0)
        assert r.decision == "SKIP"
        assert r.score == 0.0


# ---------------------------------------------------------------------------
# 2.5 — Fee-adjusted edge tests
# ---------------------------------------------------------------------------


class TestFeeAdjustedEdge:
    def test_skip_passthrough(self):
        """SKIP decision passes through unchanged."""
        e = fee_adjusted_edge("SKIP", 0.50, 0.50)
        assert e.final_decision == "SKIP"
        assert e.edge_pct == 0.0

    def test_strong_edge_no_erosion(self):
        """Model 0.72, market 0.55 → large positive edge → BET HIGHER."""
        e = fee_adjusted_edge("BET HIGHER", 0.55, 0.72)
        assert e.final_decision == "BET HIGHER"
        assert e.fee_eroded is False
        assert e.edge_pct > 10

    def test_weak_edge_erosion(self):
        """Model 0.53, market 0.50 → tiny edge below 3% → SKIP."""
        e = fee_adjusted_edge("BET HIGHER", 0.50, 0.53)
        assert e.final_decision == "SKIP"
        assert e.fee_eroded is True

    def test_negative_edge(self):
        """Model 0.53, market 0.80 → model below market → SKIP."""
        e = fee_adjusted_edge("BET HIGHER", 0.80, 0.53)
        assert e.final_decision == "SKIP"
        assert e.edge_pct < 0
