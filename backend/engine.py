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
    Picks the market whose eventStartTime is most recent but not yet past endDate.
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
    except Exception as e:
        log.warning(f"Gamma API request failed: {e}")
        return None

    if not events:
        return None

    now_ms = int(time.time() * 1000)
    best = None
    best_start_ms = 0

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

            # Parse eventStartTime to find the currently active market
            event_start = market.get("eventStartTime") or market.get("startDate")
            end_date = market.get("endDate")

            if not event_start or not end_date:
                continue

            from datetime import datetime, timezone
            try:
                start_dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                start_ms = int(start_dt.timestamp() * 1000)
                end_ms = int(end_dt.timestamp() * 1000)
            except (ValueError, TypeError):
                continue

            # Must be currently active (now between start and end)
            if now_ms < start_ms or now_ms >= end_ms:
                continue

            # Pick the one with the latest start time (most recent hour)
            if start_ms > best_start_ms:
                best_start_ms = start_ms
                best = {
                    "token_id": tokens[0],
                    "slug": event.get("slug", ""),
                    "question": market.get("question", ""),
                    "hour_open_time": start_ms,
                    "market_id": market.get("id"),
                }

    return best


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

    try:
        df = get_binance_klines(
            symbol="BTCUSDT",
            interval="5m",
            start_time=start_ms,
            end_time=now_ms,
            limit=50,
        )
        return df if df is not None and len(df) > 0 else None
    except Exception as e:
        log.warning(f"Binance price fetch failed: {e}")
        return None


def generate_signal() -> LiveSignal:
    """Generate the current live signal.

    Chains: market discovery → odds → prices → indicators → scoring.
    """
    from indicators import (
        calculate_rsi, ma_crossover, volume_spike,
        score_signal, fee_adjusted_edge,
    )

    try:
        return _generate_signal_inner()
    except Exception as e:
        log.exception("Signal generation failed")
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=0, edge_pct=0, fee_eroded=False,
            market_token_id="", market_slug="", suggested_price=None,
            minutes_remaining=0, hour_open_time=None,
            note=f"Error: {e}",
        )


def _generate_signal_inner() -> LiveSignal:

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


# --- Phase 5: Persistence ---

def persist_signal(sig: LiveSignal) -> None:
    """Write signal + paper trade + odds snapshot to Supabase (Phase 5.2)."""
    import db

    client = db.get_client()
    window = sig.hour_open_time or int(time.time() * 1000) - 3_600_000

    # Check idempotency: don't duplicate signals for same window
    existing = db.get_existing_signal(client, window)
    if existing:
        log.info(f"Signal already exists for window {window}, skipping write")
        return

    # Write signal (always)
    signal_id = db.write_signal(client, db.SignalRow(
        market_window_start=window,
        token_id=sig.market_token_id,
        decision=sig.decision,
        final_decision=sig.final_decision,
        confidence=sig.confidence,
        score=sig.score,
        model_probability=sig.model_probability,
        rsi=sig.rsi,
        ma_signal=sig.ma_signal,
        volume_signal=sig.volume_signal,
        odds=sig.odds,
        edge_pct=sig.edge_pct,
        fee_eroded=sig.fee_eroded,
        suggested_price=sig.suggested_price,
        minutes_remaining=sig.minutes_remaining,
        note=sig.note,
    ))

    # Write paper trade (only if not SKIP)
    if sig.final_decision != "SKIP":
        db.write_paper_trade(client, db.PaperTradeRow(
            signal_id=signal_id,
            market_window_start=window,
            token_id=sig.market_token_id,
            decision=sig.final_decision,
            odds=sig.odds,
            score=sig.score,
            edge_pct=sig.edge_pct,
            suggested_price=sig.suggested_price,
        ))

    # Write odds snapshot (every heartbeat tick)
    if sig.market_token_id and sig.odds > 0:
        db.write_odds_snapshot(client, sig.market_token_id, sig.odds)


def resolve_previous_hour() -> bool:
    """5.3 — Resolution checker.

    Called by heartbeat once a market hour has closed.
    Fetches Binance close price, determines outcome, updates paper_trades.
    Returns True if a trade was resolved.
    """
    import db

    client = db.get_client()
    unresolved = db.get_unresolved_trades(client)

    if not unresolved:
        return False

    resolved_count = 0
    for trade in unresolved:
        window_start = trade.get("market_window_start")
        if not window_start:
            continue

        # The hour close time = window_start + 3600000ms
        hour_close_ms = window_start + 3_600_000
        hour_close_price_ms = hour_close_ms + 59_999  # end of hour

        # Fetch the Binance candle for that hour
        try:
            from data_fetcher import get_binance_klines
            df = get_binance_klines(
                symbol="BTCUSDT",
                interval="1h",
                start_time=window_start,
                end_time=hour_close_price_ms,
                limit=1,
            )
            if df is None or len(df) == 0:
                continue

            hour_open_price = float(df.iloc[0]["open"])
            hour_close_price = float(df.iloc[0]["close"])
            actual_outcome = "UP" if hour_close_price > hour_open_price else "DOWN"

            # Determine if trade won
            trade_decision = trade.get("decision", "")
            won = (trade_decision == "BET HIGHER" and actual_outcome == "UP") or \
                  (trade_decision == "BET LOWER" and actual_outcome == "DOWN")

            # Simulate P&L (1% position sizing)
            odds = trade.get("odds", 0.50)
            if won:
                pnl_pct = (1 / odds - 1) * 1.0  # 1% of bankroll
            else:
                pnl_pct = -1.0

            db.update_paper_trade_resolution(
                client,
                signal_id=trade["signal_id"],
                resolved_outcome=actual_outcome,
                simulated_pnl=round(pnl_pct, 4),
            )
            resolved_count += 1

        except Exception as e:
            log.warning(f"Resolution failed for trade signal_id={trade.get('signal_id')}: {e}")

    log.info(f"Resolved {resolved_count}/{len(unresolved)} trades")
    return resolved_count > 0
