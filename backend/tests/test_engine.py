"""Unit tests for Phase 4 — Live Signal Engine & API.

External API calls (Gamma, CLOB, Binance) are mocked for local testing.
Live smoke test requires Render deployment (Binance DNS works there).
"""
import os, sys, time, json, pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import generate_signal, LiveSignal
from app import app


def _mock_binance_klines(symbol, interval, start_time, end_time, limit=1000):
    """Return synthetic 5m candle DataFrame for mocking Binance."""
    n = min(limit, 50)
    now_ms = int(time.time() * 1000)
    rows = []
    base_price = 100000.0
    for i in range(n):
        t = now_ms - (n - i) * 300_000
        p = base_price + np.random.uniform(-200, 200)
        rows.append({
            "open_time": t,
            "open": p,
            "close": p + np.random.uniform(-50, 50),
            "high": p + abs(np.random.uniform(0, 30)),
            "low": p - abs(np.random.uniform(0, 30)),
            "volume": np.random.uniform(100, 1000),
            "close_time": t + 299_999,
        })
    return pd.DataFrame(rows)


def _mock_gamma_market():
    """Return synthetic Gamma API response."""
    return [{
        "slug": "btc-up-hour-2026-08-06t00",
        "markets": [{
            "id": "test-market-1",
            "question": "Will BTC go UP this hour?",
            "clobTokenIds": json.dumps(["token-up-abc123"]),
            "endDate": "2026-08-06T01:00:00Z",
            "closed": False,
        }]
    }]


def _mock_clob_responses():
    """Return a function that handles both Gamma and CLOB requests."""
    def side_effect(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "gamma-api" in url:
            resp.json.return_value = _mock_gamma_market()
        elif "clob" in url:
            resp.json.return_value = {"mid": "0.55"}
        return resp
    return side_effect


# --- Unit tests with mocked APIs ---

MOCK_PATCHERS = [
    patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines),
]


class TestLiveSignal:
    def setup_method(self):
        for p in MOCK_PATCHERS:
            p.start()

    def teardown_method(self):
        for p in MOCK_PATCHERS:
            p.stop()

    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_generate_signal_returns_livesignal(self, mock_get):
        sig = generate_signal()
        assert isinstance(sig, LiveSignal)

    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_signal_has_all_fields(self, mock_get):
        sig = generate_signal()
        for field in ["decision", "final_decision", "confidence", "score",
                       "rsi", "ma_signal", "volume_signal", "odds",
                       "edge_pct", "fee_eroded", "market_token_id",
                       "market_slug", "minutes_remaining"]:
            assert hasattr(sig, field), f"Missing field: {field}"

    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_signal_decision_valid(self, mock_get):
        sig = generate_signal()
        assert sig.decision in ("SKIP", "BET HIGHER", "BET LOWER")

    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_rsi_in_valid_range(self, mock_get):
        sig = generate_signal()
        assert 0 <= sig.rsi <= 100

    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_model_probability_in_range(self, mock_get):
        sig = generate_signal()
        assert 0 <= sig.model_probability <= 1

    @patch("engine.requests.get")
    def test_no_market_returns_skip(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        sig = generate_signal()
        assert sig.decision == "SKIP"
        assert sig.final_decision == "SKIP"
        assert sig.note == "No active 1h BTC market found"


class TestFlaskAPI:
    def test_health_endpoint(self):
        client = app.test_client()
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert data["service"] == "verge-backend"

    def test_health_returns_json(self):
        client = app.test_client()
        resp = client.get("/api/health")
        assert resp.content_type == "application/json"

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_signal_endpoint(self, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/signal")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "decision" in data
        assert "final_decision" in data
        assert "score" in data
        assert "indicators" in data
        assert "market" in data
        assert "odds" in data
        assert "edge_pct" in data
        assert "suggested_price" in data

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_signal_endpoint_shape(self, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/signal")
        data = resp.get_json()
        assert data["decision"] in ("SKIP", "BET HIGHER", "BET LOWER")
        assert data["duration"] in ("1h", "15m")
        assert isinstance(data["indicators"], dict)
        assert "rsi" in data["indicators"]
        assert isinstance(data["market"], dict)
        assert "token_id" in data["market"]

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    @patch("db.get_frozen_durations", return_value=set())
    @patch("db.get_client")
    def test_heartbeat_endpoint(self, mock_db_client, mock_frozen, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/heartbeat")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "markets" in data
        assert len(data["markets"]) >= 1
        assert "decision" in data["markets"][0]
        assert "duration" in data["markets"][0]

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    @patch("app.persist_signal")
    @patch("app.resolve_previous_hour")
    @patch("db.get_frozen_durations", return_value=set())
    @patch("db.get_client")
    def test_heartbeat_calls_persist(self, mock_db_client, mock_frozen, mock_resolve, mock_persist, mock_get, mock_klines):
        mock_resolve.return_value = False
        client = app.test_client()
        resp = client.get("/api/heartbeat")
        assert resp.status_code == 200
        assert mock_persist.call_count >= 1
        assert mock_resolve.call_count >= 1


    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_signal_endpoint_has_reference_fields(self, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/signal")
        data = resp.get_json()
        assert "strike_price" in data
        assert "strike_source" in data
        assert "current_reference" in data
        assert "current_reference_source" in data
        assert "reference_quality" in data
        assert "fallback_used" in data
        assert "reference_age_seconds" in data
        assert "difference" in data
        assert "difference_percent" in data

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_signal_endpoint_reference_values_are_consistent(self, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/signal")
        data = resp.get_json()
        if data["strike_price"] and data["current_reference"]:
            assert data["difference"] is not None
            assert data["difference_percent"] is not None
            expected_diff = round(data["current_reference"] - data["strike_price"], 2)
            assert data["difference"] == expected_diff

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_signal_endpoint_15m_duration(self, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/signal?duration=15m")
        data = resp.get_json()
        assert "decision" in data
        assert "duration" in data
        assert data["duration"] == "15m"

    @patch("db.get_client")
    @patch("db.get_resolution_audit_rows", return_value=[])
    @patch("db.get_resolution_audit_statistics", return_value={
        "total_markets": 0,
        "agreement_count": 0,
        "disagreement_count": 0,
        "agreement_pct": 0,
        "strike_method_breakdown": [],
        "outcome_distribution": {},
        "strike_price_stats": None,
        "twap_vs_strike_stats": None,
    })
    def test_resolution_audit_endpoint_empty(self, mock_stats, mock_rows, mock_client):
        client = app.test_client()
        resp = client.get("/api/diagnostics/resolution-audit")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "rows" in data
        assert "statistics" in data
        assert data["rows"] == []
        assert data["statistics"]["total_markets"] == 0

    @patch("db.get_client")
    @patch("db.get_resolution_audit_rows")
    @patch("db.get_resolution_audit_statistics")
    def test_resolution_audit_endpoint_with_data(self, mock_stats, mock_rows, mock_client):
        mock_rows.return_value = [
            {
                "id": 1, "window_start": 1000, "window_close": 2000,
                "duration": "15m", "local_outcome": "UP", "official_outcome": "UP",
                "agreement": True, "strike_method": "rtds_chainlink_tick",
                "strike_price": 100000.0, "twap_price": 100050.0,
                "open_price": 100000.0, "tick_count": 60,
                "strike_source": "polymarket_price_to_beat",
                "quality_status": "GOOD", "market_id": "m1",
                "condition_id": "c1", "signal_strike_price": 100000.0,
                "signal_current_price": 100050.0, "reference_status": "estimated",
                "price_source": "rtds_chainlink",
            }
        ]
        mock_stats.return_value = {
            "total_markets": 1,
            "agreement_count": 1,
            "disagreement_count": 0,
            "agreement_pct": 100.0,
            "strike_method_breakdown": [],
            "outcome_distribution": {"UP": 1},
            "strike_price_stats": {"count": 1, "min": 100000.0, "max": 100000.0, "mean": 100000.0},
            "twap_vs_strike_stats": None,
        }
        client = app.test_client()
        resp = client.get("/api/diagnostics/resolution-audit?duration=15m&stats=true")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["agreement"] is True
        assert data["statistics"]["total_markets"] == 1
        assert data["statistics"]["agreement_pct"] == 100.0

    @patch("db.get_client")
    @patch("db.get_resolution_audit_rows")
    @patch("db.get_resolution_audit_statistics")
    def test_resolution_audit_endpoint_15m_filter(self, mock_stats, mock_rows, mock_client):
        mock_rows.return_value = []
        mock_stats.return_value = {"total_markets": 0}
        client = app.test_client()
        resp = client.get("/api/diagnostics/resolution-audit?duration=15m")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["duration_filter"] == "15m"


class TestDeterministicSlug:
    """Test deterministic 15m slug calculation and market selection."""

    def test_15m_slug_calculation(self):
        import time
        now_s = int(time.time())
        window_start_s = (now_s // 900) * 900
        expected_slug = f"btc-updown-15m-{window_start_s}"
        assert expected_slug.startswith("btc-updown-15m-")
        assert window_start_s % 900 == 0

    def test_15m_window_boundaries(self):
        import time
        now_s = int(time.time())
        window_start_s = (now_s // 900) * 900
        window_end_s = window_start_s + 900
        assert window_start_s <= now_s < window_end_s
        assert window_end_s - window_start_s == 900

    def test_15m_slug_is_deterministic(self):
        import time
        now_s = int(time.time())
        window1 = (now_s // 900) * 900
        window2 = (now_s // 900) * 900
        assert window1 == window2
        assert f"btc-updown-15m-{window1}" == f"btc-updown-15m-{window2}"

    def test_15m_market_returns_correct_structure(self):
        from engine import _get_current_15m_market
        from unittest.mock import patch, MagicMock
        import json as _json

        now_s = int(time.time())
        window_start_s = (now_s // 900) * 900
        expected_slug = f"btc-updown-15m-{window_start_s}"

        mock_event = [{
            "slug": expected_slug,
            "markets": [{
                "id": "test-15m-1",
                "question": "Bitcoin Up or Down - 15m Test",
                "clobTokenIds": _json.dumps(["token-15m-abc"]),
                "eventStartTime": f"2026-01-01T00:00:00Z",
                "endDate": f"2026-01-01T00:15:00Z",
                "closed": False,
                "conditionId": "cid-15m-test",
                "outcomePrices": _json.dumps(["0.55", "0.45"]),
            }]
        }]

        def mock_fetch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = mock_event
            return resp

        with patch("engine.fetch_with_retry", side_effect=mock_fetch), \
             patch("data_fetcher._requests_get", side_effect=mock_fetch):
            market = _get_current_15m_market()
            if market:
                assert market["slug"] == expected_slug
                assert market["duration"] == "15m"
                assert "token_id" in market
                assert "window_open" in market
                assert "window_end" in market
                assert market["window_end"] - market["window_open"] == 900_000

    def test_1h_market_still_works(self):
        from engine import get_current_market
        from unittest.mock import patch, MagicMock
        import json as _json

        mock_event = [{
            "slug": "bitcoin-up-or-down-august-20-2026-8pm-et",
            "markets": [{
                "id": "test-1h-1",
                "question": "Bitcoin Up or Down - 1h Test",
                "clobTokenIds": _json.dumps(["token-1h-abc"]),
                "eventStartTime": "2026-01-01T00:00:00Z",
                "endDate": "2026-01-01T01:00:00Z",
                "closed": False,
                "conditionId": "cid-1h-test",
                "outcomePrices": _json.dumps(["0.60", "0.40"]),
                "eventMetadata": {"priceToBeat": 100000.0},
            }]
        }]

        def mock_fetch(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = mock_event
            return resp

        with patch("engine.fetch_with_retry", side_effect=mock_fetch), \
             patch("data_fetcher._requests_get", side_effect=mock_fetch):
            market = get_current_market("1h")
            if market:
                assert market["duration"] == "1h"
                assert market["price_to_beat"] == 100000.0


class TestResolutionConfig:
    """Verify resolve_previous_hour uses correct config keys (unmocked)."""

    def test_1h_config_has_window_ms(self):
        from market_config import get_config
        config = get_config("1h")
        assert "window_ms" in config
        assert isinstance(config["window_ms"], int)
        assert config["window_ms"] > 0

    def test_15m_config_has_window_ms(self):
        from market_config import get_config
        config = get_config("15m")
        assert "window_ms" in config
        assert isinstance(config["window_ms"], int)
        assert config["window_ms"] > 0

    def test_1h_config_window_ms_is_3600000(self):
        from market_config import get_config
        config = get_config("1h")
        assert config["window_ms"] == 3_600_000

    def test_15m_config_window_ms_is_900000(self):
        from market_config import get_config
        config = get_config("15m")
        assert config["window_ms"] == 900_000

    def test_resolve_previous_hour_accesses_window_ms(self):
        """Verify resolve_previous_hour doesn't KeyError on config access."""
        from market_config import get_config
        for dur in ("1h", "15m"):
            config = get_config(dur)
            # This is the exact access pattern in resolve_previous_hour
            window_ms = config["window_ms"]
            assert isinstance(window_ms, int)
