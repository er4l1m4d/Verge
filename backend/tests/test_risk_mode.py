"""Tests for risk mode isolation from graduation gate.

Phase 3.2: Risk mode trades must never count toward the graduation gate,
regardless of how many profitable risk-mode trades accumulate.
"""
from unittest.mock import MagicMock, patch, PropertyMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestRiskModeGraduationIsolation:
    """Risk mode's trade count and ROI must be structurally incapable
    of unlocking real-money order prep."""

    def _make_safe_trades(self, count):
        """Create fake safe-mode trade rows."""
        return [
            {
                "mode": "safe",
                "resolved_outcome": "UP",
                "simulated_pnl": 0.50,
                "market_window_start": 1000000 + i * 3600000,
                "market_duration": "1h",
            }
            for i in range(count)
        ]

    def _make_risk_trades(self, count, pnl=1.0):
        """Create fake risk-mode trade rows with positive P&L."""
        return [
            {
                "mode": "risk",
                "resolved_outcome": "UP",
                "simulated_pnl": pnl,
                "market_window_start": 1000000 + i * 900000,
                "market_duration": "15m",
            }
            for i in range(count)
        ]

    def test_risk_mode_cannot_unlock_graduation(self):
        """200+ risk trades with positive P&L must NOT unlock graduation."""
        from db import get_stats

        # Setup: 50 safe trades (below 200 threshold) + 250 risk trades (above threshold)
        safe_trades = self._make_safe_trades(50)
        risk_trades = self._make_risk_trades(250, pnl=2.0)
        all_trades = safe_trades + risk_trades

        mock_client = MagicMock()

        # Mock the query chain for all trades
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_order = MagicMock()
        mock_select.order.return_value = mock_order
        mock_order.execute.return_value.data = all_trades

        # Mock the safe-mode query chain (separate query inside get_stats)
        mock_safe_result = MagicMock()
        mock_safe_result.data = safe_trades

        # The safe-mode query goes: table("paper_trades").select(...).eq("mode", "safe")...
        # We need to intercept this chain
        mock_safe_table = MagicMock()
        mock_safe_select = MagicMock()
        mock_safe_table.select.return_value = mock_safe_select
        mock_safe_eq = MagicMock()
        mock_safe_select.eq.return_value = mock_safe_eq
        mock_safe_eq.execute.return_value = mock_safe_result

        # When get_stats calls client.table("paper_trades") for safe query,
        # it should return the safe-only chain
        call_count = [0]
        def table_side_effect(name):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_table  # First call: all trades
            return mock_safe_table  # Second call: safe trades only

        mock_client.table.side_effect = table_side_effect

        result = get_stats(mock_client, mode=None)

        # unlock_real_orders should be False because safe trades < 200
        assert result["unlock_real_orders"] is False, (
            "Risk mode trades must not count toward graduation gate"
        )

    def test_safe_mode_graduation_ignores_risk(self):
        """Graduation gate only counts safe-mode trades."""
        from db import get_stats

        # 200 safe trades with positive P&L → should unlock
        safe_trades = self._make_safe_trades(200)
        risk_trades = self._make_risk_trades(500, pnl=-5.0)  # Risk mode losing money
        all_trades = safe_trades + risk_trades

        mock_client = MagicMock()

        # Mock the query chain for all trades
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_order = MagicMock()
        mock_select.order.return_value = mock_order
        mock_order.execute.return_value.data = all_trades

        # Mock the safe-mode query chain
        mock_safe_result = MagicMock()
        mock_safe_result.data = safe_trades

        mock_safe_table = MagicMock()
        mock_safe_select = MagicMock()
        mock_safe_table.select.return_value = mock_safe_select
        mock_safe_eq = MagicMock()
        mock_safe_select.eq.return_value = mock_safe_eq
        mock_safe_eq.execute.return_value = mock_safe_result

        call_count = [0]
        def table_side_effect(name):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_table
            return mock_safe_table

        mock_client.table.side_effect = table_side_effect

        result = get_stats(mock_client, mode=None)

        # Should be True because 200 safe trades exist with positive P&L
        assert result["unlock_real_orders"] is True, (
            "200 safe trades with positive P&L should unlock graduation"
        )

    def test_generate_risk_signal_never_skips(self):
        """Risk mode signal must never produce SKIP decision."""
        from engine import LiveSignal, generate_risk_signal

        safe_sig = LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0.15, model_probability=0.52, rsi=51.0,
            ma_signal=0, volume_signal=0, odds=0.50, edge_pct=0.0,
            fee_eroded=False, market_token_id="test_token",
            market_slug="test-slug", suggested_price=None,
            minutes_remaining=10, seconds_remaining=600,
            hour_open_time=1000000, hour_end_time=1036000, duration="1h",
        )

        risk_sig = generate_risk_signal(safe_sig)

        assert risk_sig.final_decision != "SKIP", (
            f"Risk mode must not SKIP, got: {risk_sig.final_decision}"
        )
        assert risk_sig.confidence == "forced", (
            f"Risk mode confidence must be 'forced', got: {risk_sig.confidence}"
        )
        assert risk_sig.mode == "risk", (
            f"Risk mode must set mode='risk', got: {risk_sig.mode}"
        )

    def test_generate_risk_signal_direction_from_score(self):
        """Risk mode direction must match score's sign."""
        from engine import LiveSignal, generate_risk_signal

        # Positive score → BET HIGHER
        positive_sig = LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0.20, model_probability=0.53, rsi=52.0,
            ma_signal=1, volume_signal=0, odds=0.50, edge_pct=0.0,
            fee_eroded=False, market_token_id="t", market_slug="s",
            suggested_price=None, minutes_remaining=10, seconds_remaining=600,
            hour_open_time=1000000, hour_end_time=1036000, duration="1h",
        )
        risk_pos = generate_risk_signal(positive_sig)
        assert risk_pos.final_decision == "BET HIGHER"

        # Negative score → BET LOWER
        negative_sig = LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=-0.20, model_probability=0.47, rsi=48.0,
            ma_signal=-1, volume_signal=0, odds=0.50, edge_pct=0.0,
            fee_eroded=False, market_token_id="t", market_slug="s",
            suggested_price=None, minutes_remaining=10, seconds_remaining=600,
            hour_open_time=1000000, hour_end_time=1036000, duration="1h",
        )
        risk_neg = generate_risk_signal(negative_sig)
        assert risk_neg.final_decision == "BET LOWER"

        # Zero score → BET HIGHER (tiebreak)
        zero_sig = LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0.0, model_probability=0.50, rsi=50.0,
            ma_signal=0, volume_signal=0, odds=0.50, edge_pct=0.0,
            fee_eroded=False, market_token_id="t", market_slug="s",
            suggested_price=None, minutes_remaining=10, seconds_remaining=600,
            hour_open_time=1000000, hour_end_time=1036000, duration="1h",
        )
        risk_zero = generate_risk_signal(zero_sig)
        assert risk_zero.final_decision == "BET HIGHER", (
            "Zero score tiebreak must be BET HIGHER"
        )

    def test_risk_signal_same_suggested_price_as_safe(self):
        """Risk mode uses same position sizing as safe mode."""
        from engine import LiveSignal, generate_risk_signal

        sig = LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0.30, model_probability=0.55, rsi=55.0,
            ma_signal=1, volume_signal=1, odds=0.60, edge_pct=2.0,
            fee_eroded=False, market_token_id="t", market_slug="s",
            suggested_price=None, minutes_remaining=10, seconds_remaining=600,
            hour_open_time=1000000, hour_end_time=1036000, duration="1h",
        )

        risk_sig = generate_risk_signal(sig)

        # Suggested price should be odds * discount (0.60 * 0.95 = 0.57)
        assert risk_sig.suggested_price == round(0.60 * 0.95, 2), (
            f"Risk mode suggested_price should match safe mode sizing, "
            f"got {risk_sig.suggested_price}"
        )
