"""Live Signal Engine — Phase 4.3.

Orchestrates market discovery, odds fetch, price fetch, and scoring
into a single call that returns the current signal.
"""
import os
import sys
import time
import json
import logging
import requests
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__))

log = logging.getLogger("verge.engine")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


@dataclass
class LiveSignal:
    """Complete live signal output."""
    decision: str
    final_decision: str
    confidence: str
    score: float
    model_probability: float
    rsi: float
    ma_signal: int
    volume_signal: int
    odds: float
    edge_pct: float
    fee_eroded: bool
    market_token_id: str
    market_slug: str
    suggested_price: float | None
    minutes_remaining: int
    hour_open_time: int | None
    note: str | None = None


def get_current_hourly_market() -> dict | None:
    """4.1 — Find the currently active hourly BTC market on Polymarket.

    Queries Gamma API for active (unresolved) events in the hourly BTC series.
    Returns dict with keys: token_id, slug, hour_open_time, question.
    """
    params = {
        "limit": 20,
        "series_slug": "btc-up-or-down-hourly",
        "closed": "false",
    }
    try:
        resp = requests.get(f"{GAMMA_API}/events", params=params, timeout=30)
        resp.raise_for_status()
        events = resp.json()
    except requests.exceptions.RequestException as e:
        log.warning(f"Gamma API request failed: {e}")
        return None

    if not events:
        return None

    # Pick the first active event with valid tokens
    for event in events:
        for market in event.get("markets", []):
            if market.get("closed", True):
                continue

            tokens_raw = market.get("clobTokenIds", "[]")
            if isinstance(tokens_raw, str):
                try:
                    tokens = json.loads(tokens_raw)
                except json.JSONDecodeError:
                    tokens = []
            else:
                tokens = tokens_raw

            if not tokens:
                continue

            # Parse hour open time from end_date_iso or question
            end_date = market.get("endDate")
            hour_open_time = None
            if end_date:
                # end_date is ISO string, convert to ms timestamp
                from datetime import datetime, timezone
                try:
                    dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    # Hour market ends at the hour, open was 1 hour before
                    hour_open_time = int((dt.timestamp() - 3600) * 1000)
                except (ValueError, TypeError):
                    pass

            return {
                "token_id": tokens[0],
                "slug": event.get("slug", ""),
                "question": market.get("question", ""),
                "hour_open_time": hour_open_time,
                "market_id": market.get("id"),
            }

    return None


def get_current_odds(token_id: str) -> float | None:
    """4.2 — Get current best odds for a token from CLOB.

    Returns implied probability (0-1) or None.
    """
    try:
        resp = requests.get(
            f"{CLOB_API}/midpoint",
            params={"token_id": token_id},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        mid = float(data.get("mid", 0))
        if 0 < mid < 1:
            return mid
    except requests.exceptions.RequestException:
        pass

    # Fallback: try prices-history
    try:
        resp = requests.get(
            f"{CLOB_API}/prices-history",
            params={"market": token_id, "interval": "1m", "fidelity": 1},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        history = data.get("history", [])
        if history:
            return float(history[-1].get("p", 0.5))
    except requests.exceptions.RequestException:
        pass

    return None


def get_current_price_data():
    """4.3 — Fetch recent Binance 5m candles for indicator computation.

    Returns a DataFrame ready for the indicator functions.
    """
    import time as _time
    from data_fetcher import get_binance_klines

    now_ms = int(_time.time() * 1000)
    start_ms = now_ms - (50 * 5 * 60 * 1000)  # 50 candles back

    df = get_binance_klines(
        symbol="BTCUSDT",
        interval="5m",
        start_time=start_ms,
        end_time=now_ms,
        limit=50,
    )
    return df if df is not None and len(df) > 0 else None


def generate_signal() -> LiveSignal:
    """Generate the current live signal.

    Chains: market discovery → odds → prices → indicators → scoring.
    """
    from indicators import (
        calculate_rsi, ma_crossover, volume_spike,
        score_signal, fee_adjusted_edge,
    )

    # 4.1 — Market discovery
    market = get_current_hourly_market()
    if market is None:
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=0, edge_pct=0, fee_eroded=False,
            market_token_id="", market_slug="", suggested_price=None,
            minutes_remaining=0, hour_open_time=None,
            note="No active hourly BTC market found",
        )

    token_id = market["token_id"]
    slug = market["slug"]
    hour_open_time = market.get("hour_open_time")

    # Compute minutes remaining
    if hour_open_time:
        now_ms = int(time.time() * 1000)
        elapsed_ms = now_ms - hour_open_time
        minutes_remaining = max(0, 60 - int(elapsed_ms / 60_000))
    else:
        minutes_remaining = 0

    # 4.2 — Current odds
    odds = get_current_odds(token_id)
    if odds is None:
        odds = 0.50  # fallback to even odds

    # 4.3 — Recent 5m candles
    df_5m = get_current_price_data()
    if df_5m is None or len(df_5m) < 16:
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=odds, edge_pct=0, fee_eroded=False,
            market_token_id=token_id, market_slug=slug,
            suggested_price=None, minutes_remaining=minutes_remaining,
            hour_open_time=hour_open_time,
            note="Insufficient price data",
        )

    # Compute indicators
    prices = df_5m["close"].tolist()
    volumes = df_5m["volume"].tolist()

    rsi = calculate_rsi(prices)
    ma_sig = ma_crossover(prices)

    # Volume spike
    if len(volumes) >= 10:
        avg_vol = sum(volumes[-11:-1]) / 10
        current_vol = volumes[-1]
        last_candle = df_5m.iloc[-1]
        candle_dir = 1 if last_candle["close"] > last_candle["open"] else -1
        vol_sig = volume_spike(current_vol, avg_vol, candle_dir)
    else:
        vol_sig = 0

    # Score
    sig = score_signal(rsi, ma_sig, vol_sig)

    # Fee-adjusted edge
    edge = fee_adjusted_edge(sig.decision, odds, sig.model_probability)

    # Suggested limit price: if BET HIGHER, bid below current odds
    suggested_price = None
    if edge.final_decision == "BET HIGHER":
        suggested_price = round(odds * 0.95, 2)  # 5% discount
    elif edge.final_decision == "BET LOWER":
        suggested_price = round((1 - odds) * 0.95, 2)

    return LiveSignal(
        decision=sig.decision,
        final_decision=edge.final_decision,
        confidence=sig.confidence,
        score=sig.score,
        model_probability=sig.model_probability,
        rsi=rsi,
        ma_signal=ma_sig,
        volume_signal=vol_sig,
        odds=odds,
        edge_pct=edge.edge_pct,
        fee_eroded=edge.fee_eroded,
        market_token_id=token_id,
        market_slug=slug,
        suggested_price=suggested_price,
        minutes_remaining=minutes_remaining,
        hour_open_time=hour_open_time,
    )
