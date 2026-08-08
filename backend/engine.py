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

from market_config import get_config, supported_durations

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
    seconds_remaining: int
    hour_open_time: int | None
    hour_end_time: int | None
    strike_price: float | None = None
    current_price: float | None = None
    note: str | None = None
    duration: str = "1h"


def get_current_market(duration: str = "1h") -> dict | None:
    """4.1 — Find the currently active BTC market for a given duration.

    Queries Gamma API for active (unresolved) events, using the series_slug
    from market_config. Falls back to slug-prefix matching if the series
    query returns nothing.

    Args:
        duration: "1h" or "15m" (keys from market_config.MARKET_CONFIG)

    Returns dict with keys: token_id, slug, question, window_open, window_end,
    duration. Also includes hour_open_time/hour_end_time for backward compat.
    """
    config = get_config(duration)
    series_slug = config["series_slug"]
    slug_prefix = config["slug_prefix"]
    window_ms = config["window_ms"]

    # --- Primary: series_slug query ---
    events = _fetch_events_by_series(series_slug)

    # --- Fallback: slug-prefix matching ---
    if not events:
        log.info(f"Series query empty for '{series_slug}', trying slug-prefix fallback")
        events = _fetch_events_by_slug_prefix(slug_prefix)

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

            # Pick the one with the latest start time (most recent window)
            if start_ms > best_start_ms:
                best_start_ms = start_ms

                # Extract priceToBeat from event metadata (Polymarket's official strike)
                metadata = event.get("eventMetadata") or {}
                price_to_beat = metadata.get("priceToBeat")

                # Extract outcomePrices from market (Polymarket's official odds)
                # Format: "[\"0.9995\", \"0.0005\"]" — first is "Up" probability
                outcome_prices_raw = market.get("outcomePrices")
                up_odds = None
                if outcome_prices_raw:
                    try:
                        prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                        if prices:
                            up_odds = float(prices[0])
                    except (json.JSONDecodeError, IndexError, TypeError):
                        pass

                best = {
                    "token_id": tokens[0],
                    "slug": event.get("slug", ""),
                    "question": market.get("question", ""),
                    "window_open": start_ms,
                    "window_end": end_ms,
                    "duration": duration,
                    "price_to_beat": price_to_beat,
                    "up_odds": up_odds,
                    # Backward compat — engine.py still uses these names
                    "hour_open_time": start_ms,
                    "hour_end_time": end_ms,
                }

    return best


def _fetch_events_by_series(series_slug: str) -> list:
    """Fetch events from Gamma API filtered by series_slug."""
    params = {
        "limit": 20,
        "series_slug": series_slug,
        "closed": "false",
    }
    try:
        resp = requests.get(f"{GAMMA_API}/events", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"Gamma API request failed for series '{series_slug}': {e}")
        return []


def _fetch_events_by_slug_prefix(slug_prefix: str) -> list:
    """4.2 — Fallback: fetch recent events and filter by slug prefix.

    Pulls a broader page of recent events from Gamma (no series filter)
    and filters client-side for slugs starting with the given prefix.
    """
    params = {
        "limit": 100,
        "closed": "false",
        "order": "startDate",
        "ascending": "false",
    }
    try:
        resp = requests.get(f"{GAMMA_API}/events", params=params, timeout=30)
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        log.warning(f"Gamma API slug-prefix fallback failed: {e}")
        return []

    # Filter for events whose slug starts with the prefix
    filtered = [e for e in events if e.get("slug", "").startswith(slug_prefix)]
    if filtered:
        log.info(
            f"Slug-prefix fallback found {len(filtered)} events "
            f"matching '{slug_prefix}*' (from {len(events)} total)"
        )
    return filtered


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


def get_current_price_data_for_duration(config: dict):
    """Fetch price candles for the given duration's config.

    For 1h: Binance 5m candles (existing behavior).
    For 15m: Chainlink 1m bars via chainlink_fetcher, reading cached ticks
    from price_snapshots table (Phase 7.2b).
    """
    import time as _time

    if config["price_source"] == "chainlink":
        # 15m path: use Chainlink on-chain feed + Binance bootstrap
        from chainlink_fetcher import get_chainlink_bars

        # Read cached ticks from price_snapshots table
        cached_ticks = []
        try:
            import db
            client = db.get_client()
            since_ms = int(_time.time() * 1000) - (config["bar_lookback"] + 10) * 60_000
            raw_ticks = db.get_price_snapshots(
                client, source="chainlink", symbol="BTC",
                since_ms=since_ms, limit=500,
            )
            cached_ticks = [{"timestamp_ms": t["timestamp_ms"], "price": t["price"]} for t in raw_ticks]
        except Exception as e:
            log.warning(f"Failed to read cached ticks (non-fatal): {e}")

        df = get_chainlink_bars(
            cached_ticks=cached_ticks,
            interval_ms=60_000,  # 1-minute bars
            min_bars=config["min_candles"],
        )
        return df if df is not None and len(df) > 0 else None
    else:
        # 1h path: Binance 5m candles (existing behavior)
        from data_fetcher import get_price_with_fallback

        now_ms = int(_time.time() * 1000)
        start_ms = now_ms - (config["bar_lookback"] * 5 * 60 * 1000)

        try:
            df = get_price_with_fallback(
                symbol="BTCUSDT",
                interval=config["bar_interval"],
                start_time=start_ms,
                end_time=now_ms,
            )
            return df if df is not None and len(df) > 0 else None
        except Exception as e:
            log.warning(f"Price fetch failed: {e}")
            return None


def generate_signal(duration: str = "1h") -> LiveSignal:
    """Generate the current live signal for a given duration.

    Chains: market discovery → odds → prices → indicators → scoring.

    Args:
        duration: "1h" or "15m" (keys from market_config.MARKET_CONFIG)
    """
    try:
        return _generate_signal_inner(duration)
    except Exception as e:
        log.exception("Signal generation failed")
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=0, edge_pct=0, fee_eroded=False,
            market_token_id="", market_slug="", suggested_price=None,
            minutes_remaining=0, seconds_remaining=0,
            hour_open_time=None, hour_end_time=None,
            note=f"Error: {e}",
            duration=duration,
        )


def _generate_signal_inner(duration: str = "1h") -> LiveSignal:
    from indicators import (
        calculate_rsi, ma_crossover, volume_spike,
        score_signal, fee_adjusted_edge,
    )
    from data_fetcher import get_spot_price

    config = get_config(duration)

    # 4.1 — Market discovery
    market = get_current_market(duration)
    if market is None:
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=0, edge_pct=0, fee_eroded=False,
            market_token_id="", market_slug="", suggested_price=None,
            minutes_remaining=0, seconds_remaining=0,
            hour_open_time=None, hour_end_time=None,
            note=f"No active {duration} BTC market found",
            duration=duration,
        )

    token_id = market["token_id"]
    slug = market["slug"]
    hour_open_time = market.get("hour_open_time")
    hour_end_time = market.get("hour_end_time")

    # Compute minutes + seconds remaining using actual end time
    now_ms = int(time.time() * 1000)
    if hour_end_time and hour_end_time > now_ms:
        remaining_ms = hour_end_time - now_ms
        minutes_remaining = int(remaining_ms / 60_000)
        seconds_remaining = int((remaining_ms % 60_000) / 1000)
    else:
        minutes_remaining = 0
        seconds_remaining = 0

    # 5.2 — No-bet-final-minutes rule
    no_bet_min = config.get("no_bet_final_minutes", 10)
    if minutes_remaining < no_bet_min and minutes_remaining >= 0:
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=0, edge_pct=0, fee_eroded=False,
            market_token_id=token_id, market_slug=slug,
            suggested_price=None, minutes_remaining=minutes_remaining,
            seconds_remaining=seconds_remaining,
            hour_open_time=hour_open_time, hour_end_time=hour_end_time,
            note=f"SKIP: {minutes_remaining}min remaining < {no_bet_min}min threshold",
            duration=duration,
        )

    # 4.2 — Current odds (prefer Gamma API outcomePrices, fallback to CLOB)
    odds = market.get("up_odds")  # from Gamma API eventMetadata
    if odds is None:
        odds = get_current_odds(token_id)  # fallback to CLOB midpoint
    if odds is None:
        odds = 0.50  # final fallback

    # 4.3 — Price data (duration-aware)
    df_price = get_current_price_data_for_duration(config)
    if df_price is None or len(df_price) < config["min_candles"]:
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=odds, edge_pct=0, fee_eroded=False,
            market_token_id=token_id, market_slug=slug,
            suggested_price=None, minutes_remaining=minutes_remaining,
            seconds_remaining=seconds_remaining,
            hour_open_time=hour_open_time, hour_end_time=hour_end_time,
            note="Insufficient price data",
            duration=duration,
        )

    # Compute indicators with duration-scaled params
    prices = df_price["close"].tolist()
    volumes = df_price["volume"].tolist()

    rsi = calculate_rsi(prices, period=config["rsi_period"])
    ma_sig = ma_crossover(prices, fast=config["ma_fast"], slow=config["ma_slow"])

    # Volume spike with duration-scaled lookback
    vol_lookback = config["volume_lookback"]
    if len(volumes) >= vol_lookback:
        avg_vol = sum(volumes[-(vol_lookback + 1):-1]) / vol_lookback
        current_vol = volumes[-1]
        last_candle = df_price.iloc[-1]
        candle_dir = 1 if last_candle["close"] > last_candle["open"] else -1
        vol_sig = volume_spike(current_vol, avg_vol, candle_dir)
    else:
        vol_sig = 0

    # Score
    sig = score_signal(rsi, ma_sig, vol_sig)

    # Fee-adjusted edge
    edge = fee_adjusted_edge(sig.decision, odds, sig.model_probability)

    # Suggested limit price
    discount = config.get("suggested_price_discount", 0.95)
    suggested_price = None
    if edge.final_decision == "BET HIGHER":
        suggested_price = round(odds * discount, 2)
    elif edge.final_decision == "BET LOWER":
        suggested_price = round((1 - odds) * discount, 2)

    # Strike + current price (prefer Polymarket's official priceToBeat)
    spot = get_spot_price()
    current_price = spot if spot else (float(df_price.iloc[-1]["close"]) if len(df_price) > 0 else None)

    # Use Polymarket's official strike if available, else compute from candles
    strike_price = market.get("price_to_beat")
    if strike_price is not None:
        log.info(f"[{duration}] Using official Polymarket strike: ${strike_price:,.2f}")
    else:
        log.info(f"[{duration}] Official strike unavailable, using fallback")
        if duration == "15m" and hour_open_time is not None:
            # 15m: Gamma doesn't provide priceToBeat — fetch BTC at window start
            from data_fetcher import get_price_at_time
            strike_price = get_price_at_time(hour_open_time)
            if strike_price:
                log.info(f"[{duration}] Strike from Coinbase at {hour_open_time}: ${strike_price:,.2f}")
        elif hour_open_time is not None and len(df_price) > 0:
            # 1h fallback: compute from first candle in window
            matching = df_price[df_price["open_time"] >= hour_open_time]
            if len(matching) > 0:
                strike_price = float(matching.iloc[0]["open"])
            else:
                strike_price = float(df_price.iloc[0]["open"])
            if strike_price:
                log.info(f"[{duration}] Strike from Binance candles: ${strike_price:,.2f}")

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
        seconds_remaining=seconds_remaining,
        hour_open_time=hour_open_time,
        hour_end_time=hour_end_time,
        strike_price=strike_price,
        current_price=current_price,
        duration=duration,
    )


# --- Phase 5: Persistence ---

def persist_signal(sig: LiveSignal) -> None:
    """Write signal + paper trade + odds snapshot to Supabase (Phase 5.2)."""
    import db

    client = db.get_client()
    window = sig.hour_open_time or int(time.time() * 1000) - 3_600_000

    # Check idempotency: don't duplicate signals for same window + duration
    existing = db.get_existing_signal(client, window, duration=sig.duration)
    if existing:
        log.info(f"Signal already exists for window {window} duration={sig.duration}, skipping write")
        return

    # Write signal (always)
    signal_id = db.write_signal(client, db.SignalRow(
        market_window_start=window,
        market_duration=sig.duration,
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
        strike_price=sig.strike_price,
        current_price=sig.current_price,
        note=sig.note,
    ))

    # Write paper trade (only if not SKIP)
    if sig.final_decision != "SKIP":
        db.write_paper_trade(client, db.PaperTradeRow(
            signal_id=signal_id,
            market_window_start=window,
            market_duration=sig.duration,
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


def record_price_tick(duration: str = "15m") -> bool:
    """Record the current Chainlink price tick to price_snapshots (Phase 7.2a).

    Called on every heartbeat for 15m duration to accumulate ticks
    for tick-based resolution and bar-building.

    Returns True if a tick was written, False on failure.
    """
    if duration != "15m":
        return False

    try:
        from chainlink_fetcher import get_chainlink_price
        import db

        price = get_chainlink_price()
        if price is None:
            log.warning("Cannot record tick: Chainlink price unavailable")
            return False

        client = db.get_client()
        db.write_price_snapshot(
            client,
            db.PriceSnapshotRow(
                source="chainlink",
                symbol="BTC",
                timestamp_ms=int(time.time() * 1000),
                price=price,
            ),
        )
        log.info(f"Recorded Chainlink tick: ${price:,.2f}")
        return True

    except Exception as e:
        log.warning(f"Price tick recording failed: {e}")
        return False


def resolve_previous_hour(duration: str = "1h") -> bool:
    """5.3 + Phase 7.2c — Resolution checker (duration-aware).

    Called by heartbeat once a market window has closed.
    Fetches price data, determines outcome, updates paper_trades.
    Returns True if a trade was resolved.

    Resolution method:
      - 1h: Binance candle open/close (unchanged)
      - 15m: Tick-based from price_snapshots table (Chainlink ticks).
             Falls back to Coinbase candles if insufficient ticks.
    """
    import db
    from market_config import get_config

    config = get_config(duration)
    window_ms = config["window_ms"]

    client = db.get_client()
    unresolved = db.get_unresolved_trades(client, duration=duration)

    if not unresolved:
        return False

    resolved_count = 0
    for trade in unresolved:
        window_start = trade.get("market_window_start")
        if not window_start:
            continue

        close_ms = window_start + window_ms

        try:
            if duration == "15m":
                # 7.2c: Tick-based resolution from price_snapshots
                ticks = db.get_price_snapshots(
                    client, source="chainlink", symbol="BTC",
                    since_ms=window_start, limit=1000,
                )
                # Filter ticks within the window
                window_ticks = [t for t in ticks if t["timestamp_ms"] <= close_ms]

                if len(window_ticks) >= 2:
                    # Use first tick as open, last tick as close
                    open_price = float(window_ticks[0]["price"])
                    close_price = float(window_ticks[-1]["price"])
                    actual_outcome = "UP" if close_price > open_price else "DOWN"
                    log.info(
                        f"15m resolution via ticks: open=${open_price:,.2f} "
                        f"close=${close_price:,.2f} -> {actual_outcome} "
                        f"({len(window_ticks)} ticks)"
                    )
                else:
                    # Insufficient ticks — skip resolution, retry on next heartbeat
                    log.warning(
                        f"Insufficient ticks for 15m resolution "
                        f"({len(window_ticks)} ticks, need ≥2), skipping"
                    )
                    continue
            else:
                # 1h: use Binance (existing behavior, unchanged)
                from data_fetcher import get_binance_klines
                df = get_binance_klines(
                    symbol="BTCUSDT",
                    interval="1h",
                    start_time=window_start,
                    end_time=close_ms,
                    limit=1,
                )
                if df is None or len(df) == 0:
                    continue
                open_price = float(df.iloc[0]["open"])
                close_price = float(df.iloc[0]["close"])
                actual_outcome = "UP" if close_price > open_price else "DOWN"

            # Determine if trade won
            trade_decision = trade.get("decision", "")
            won = (trade_decision == "BET HIGHER" and actual_outcome == "UP") or \
                  (trade_decision == "BET LOWER" and actual_outcome == "DOWN")

            # Simulate P&L (1% position sizing)
            odds = trade.get("odds", 0.50)
            if won:
                pnl_pct = (1 / odds - 1) * 1.0
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

    log.info(f"Resolved {resolved_count}/{len(unresolved)} trades (duration={duration})")
    return resolved_count > 0
