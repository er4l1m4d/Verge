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
        assert sig.note == "No active hourly BTC market found"


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
        assert isinstance(data["indicators"], dict)
        assert "rsi" in data["indicators"]
        assert isinstance(data["market"], dict)
        assert "token_id" in data["market"]

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    def test_heartbeat_endpoint(self, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/heartbeat")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "decision" in data
        assert "market" in data

    @patch("data_fetcher.get_binance_klines", side_effect=_mock_binance_klines)
    @patch("engine.requests.get", side_effect=_mock_clob_responses())
    @patch("app.persist_signal")
    @patch("app.resolve_previous_hour")
    def test_heartbeat_calls_persist(self, mock_resolve, mock_persist, mock_get, mock_klines):
        client = app.test_client()
        resp = client.get("/api/heartbeat")
        assert resp.status_code == 200
        mock_persist.assert_called_once()
        mock_resolve.assert_called_once()
