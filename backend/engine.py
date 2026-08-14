"""Live Signal Engine — Phase 4.3.

Orchestrates market discovery, odds fetch, price fetch, and scoring
into a single call that returns the current signal.
"""
import os
import sys
import time
import json
import asyncio
import logging
import requests
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(__file__))

from market_config import get_config, supported_durations
from data_fetcher import fetch_with_retry, _requests_get as _http_get

log = logging.getLogger("verge.engine")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# ── Phase 1: Robust Strike-Price Extraction ──────────────────────────

import re

_STRIKE_KEY_PATTERN = re.compile(r"(price|strike|threshold|target|beat)", re.I)
_PRICE_TO_BEAT_TEXT = re.compile(
    r"price\s*to\s*beat[^\d$]*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I
)


def extract_strike_from_market(market: dict, max_depth: int = 6) -> float | None:
    """Walk the entire market JSON looking for a strike-price field.

    Searches for any key matching price|strike|threshold|target|beat,
    bounded to max_depth levels with a seen-set for circular-reference safety.
    """
    seen = set()
    stack = [(market, 0)]
    while stack:
        obj, depth = stack.pop()
        obj_id = id(obj)
        if not isinstance(obj, (dict, list)) or obj_id in seen or depth > max_depth:
            continue
        seen.add(obj_id)

        items = enumerate(obj) if isinstance(obj, list) else obj.items()
        for key, value in items:
            if isinstance(value, (dict, list)):
                stack.append((value, depth + 1))
                continue
            if not _STRIKE_KEY_PATTERN.search(str(key)):
                continue
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            if 1_000 < n < 2_000_000:
                return n
    return None


def parse_strike_from_text(market: dict) -> float | None:
    """Regex-parse the strike from the market's question or title text."""
    text = str(market.get("question") or market.get("title") or "")
    m = _PRICE_TO_BEAT_TEXT.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


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
    divergence_signal: int = 0  # shadow mode: -1, 0, +1 (not in live score yet)
    fear_greed_value: int | None = None  # daily Fear & Greed index (0-100)
    price_source: str | None = None
    condition_id: str | None = None


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
    series_id = config.get("series_id")
    slug_prefix = config["slug_prefix"]
    window_ms = config["window_ms"]

    # --- Primary: series_slug query ---
    events = _fetch_events_by_series(series_slug)

    # --- Fallback: series_id query ---
    if not events and series_id:
        log.info(f"Series slug query empty for '{series_slug}', trying series_id fallback ({series_id})")
        events = _fetch_events_by_series_id(series_id)

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
                    "condition_id": market.get("conditionId"),
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
        resp = fetch_with_retry(
            lambda: _http_get(f"{GAMMA_API}/events", params=params, timeout=30)
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"Gamma API request failed for series '{series_slug}': {e}")
        return []


def _fetch_events_by_series_id(series_id: str) -> list:
    """Fallback: fetch events from Gamma API filtered by series_id (numeric ID)."""
    params = {
        "limit": 20,
        "series_id": series_id,
        "closed": "false",
    }
    try:
        resp = fetch_with_retry(
            lambda: _http_get(f"{GAMMA_API}/events", params=params, timeout=30)
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"Gamma API request failed for series_id '{series_id}': {e}")
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
        resp = fetch_with_retry(
            lambda: _http_get(f"{GAMMA_API}/events", params=params, timeout=30)
        )
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
        resp = fetch_with_retry(
            lambda: _http_get(
                f"{CLOB_API}/midpoint",
                params={"token_id": token_id},
                timeout=15,
            )
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
        resp = fetch_with_retry(
            lambda: _http_get(
                f"{CLOB_API}/prices-history",
                params={"market": token_id, "interval": "1m", "fidelity": 1},
                timeout=15,
            )
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

    Returns (df, fetch_failed) tuple where:
        df: DataFrame of candles or None
        fetch_failed: True if the failure was due to a network/fetch error
                      (vs genuinely insufficient data)
    """
    import time as _time

    if config["price_source"] == "chainlink":
        # 15m path: use Chainlink on-chain feed + Binance bootstrap
        from chainlink_fetcher import get_chainlink_bars

        # Read cached ticks from price_snapshots table
        cached_ticks = []
        fetch_failed = False
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
            fetch_failed = True

        df = get_chainlink_bars(
            cached_ticks=cached_ticks,
            interval_ms=60_000,  # 1-minute bars
            min_bars=config["min_candles"],
        )
        if df is not None and len(df) > 0:
            return df, False
        return None, fetch_failed
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
            if df is not None and len(df) > 0:
                return df, False
            return None, False
        except Exception as e:
            log.warning(f"Price fetch failed: {e}")
            return None, True


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
    condition_id = market.get("condition_id")
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
    df_price, price_fetch_failed = get_current_price_data_for_duration(config)
    if df_price is None or len(df_price) < config["min_candles"]:
        note = "Fetch failed after retries" if price_fetch_failed else "Insufficient price data"
        return LiveSignal(
            decision="SKIP", final_decision="SKIP", confidence="none",
            score=0, model_probability=0.5, rsi=50, ma_signal=0,
            volume_signal=0, odds=odds, edge_pct=0, fee_eroded=False,
            market_token_id=token_id, market_slug=slug,
            suggested_price=None, minutes_remaining=minutes_remaining,
            seconds_remaining=seconds_remaining,
            hour_open_time=hour_open_time, hour_end_time=hour_end_time,
            note=note,
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

    # Odds-vs-momentum divergence (shadow mode -- logged, not in score yet)
    from indicators import odds_momentum_divergence
    momentum_prices = prices[-5:] if len(prices) >= 2 else prices
    div_val = odds_momentum_divergence(odds, momentum_prices)

    # Fear & Greed (non-directional gate -- adjusts confidence thresholds only)
    from data_fetcher import get_fear_greed_index
    fg_value = get_fear_greed_index()

    # Score
    sig = score_signal(rsi, ma_sig, vol_sig, divergence_val=div_val, fear_greed_value=fg_value)

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
    # Price source chain: TWAP → Chainlink on-chain → Pyth → Coinbase spot → candle close
    import db as _db
    now_ms_val = int(time.time() * 1000)
    recent_ticks = _db.get_recent_price_snapshots(
        _db.get_client(), source="polymarket_ws_tick", symbol="BTCUSD",
        since_ms=now_ms_val - 90_000,
    )
    twap_price = compute_twap(recent_ticks, window_end_ms=now_ms_val) if len(recent_ticks) >= 3 else None

    # 1. TWAP from accumulated Polymarket WS ticks (best: same source Polymarket resolves with)
    if twap_price:
        current_price, price_source = twap_price, "polymarket_ws_twap_60s"
        log.info(f"[{duration}] TWAP price: ${twap_price:,.2f} (from {len(recent_ticks)} ticks)")
    else:
        # 2. Chainlink on-chain (direct contract read)
        from chainlink_fetcher import get_chainlink_price
        cl_price = get_chainlink_price()
        if cl_price:
            current_price, price_source = cl_price, "chainlink_onchain"
            log.info(f"[{duration}] Chainlink on-chain price: ${cl_price:,.2f}")
        else:
            # 3. Pyth oracle (different network, independent cross-check)
            from pyth_fetcher import get_pyth_btc_price_value
            pyth_price = get_pyth_btc_price_value()
            if pyth_price:
                current_price, price_source = pyth_price, "pyth"
                log.info(f"[{duration}] Pyth oracle price: ${pyth_price:,.2f}")
            else:
                # 4. Coinbase spot → candle close fallback
                spot = get_spot_price()
                if spot:
                    current_price, price_source = spot, "coinbase_spot"
                    log.info(f"[{duration}] Coinbase spot price: ${spot:,.2f}")
                elif len(df_price) > 0:
                    current_price, price_source = float(df_price.iloc[-1]["close"]), "candle_close"
                    log.info(f"[{duration}] Candle close price: ${current_price:,.2f}")
                else:
                    current_price, price_source = None, None

    # Use Polymarket's official strike if available, else compute from candles
    strike_price = market.get("price_to_beat")
    if strike_price is not None:
        log.info(f"[{duration}] Using official Polymarket strike: ${strike_price:,.2f}")
    else:
        # Phase 1: Recursive key search + text parsing fallback
        strike_price = extract_strike_from_market(market)
        if strike_price is not None:
            log.info(f"[{duration}] Strike from recursive key search: ${strike_price:,.2f}")
        else:
            strike_price = parse_strike_from_text(market)
            if strike_price is not None:
                log.info(f"[{duration}] Strike from text parsing: ${strike_price:,.2f}")

    if strike_price is None:
        log.info(f"[{duration}] All extraction methods failed, using candle/Chainlink fallback")
        if duration == "15m" and hour_open_time is not None:
            # 15m: Prefer Chainlink bars' open (matches Polymarket's resolution source)
            if len(df_price) > 0:
                matching = df_price[df_price["open_time"] >= hour_open_time]
                if len(matching) > 0:
                    strike_price = float(matching.iloc[0]["open"])
                    log.info(f"[15m] Strike from Chainlink bars: ${strike_price:,.2f}")
            # Fallback: Coinbase if Chainlink bars unavailable
            if strike_price is None:
                from data_fetcher import get_price_at_time
                strike_price = get_price_at_time(hour_open_time)
                if strike_price:
                    log.info(f"[15m] Strike from Coinbase fallback: ${strike_price:,.2f}")
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
        divergence_signal=div_val,
        fear_greed_value=fg_value,
        price_source=price_source,
        condition_id=condition_id,
    )



# --- Phase 5: Persistence ---

def persist_signal(sig: LiveSignal) -> tuple[int | None, str]:
    """Write signal + paper trade + odds snapshot to Supabase (Phase 5.2).

    Returns (signal_id, status) where status is 'created', 'duplicate', or 'error'.
    """
    import db

    client = db.get_client()
    window = sig.hour_open_time or int(time.time() * 1000) - 3_600_000
    mode = "safe"

    # Check idempotency: don't duplicate signals for same window + duration + mode
    existing = db.get_existing_signal(client, window, duration=sig.duration, mode=mode)
    if existing:
        log.info(f"Signal already exists for window {window} duration={sig.duration}, skipping write")
        return None, "duplicate"

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
        divergence_signal=sig.divergence_signal,
        fear_greed_value=sig.fear_greed_value,
        mode=mode,
        price_source=sig.price_source,
        condition_id=sig.condition_id,
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
            mode=mode,
        ))

    # Write odds snapshot (every heartbeat tick)
    try:
        if sig.market_token_id and sig.odds > 0:
            db.write_odds_snapshot(client, sig.market_token_id, sig.odds)
    except Exception as e:
        log.warning(f"Odds snapshot write failed (non-fatal): {e}")

    return signal_id, "created"


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


def record_polymarket_ws_tick() -> bool:
    """Fetch Polymarket WS price and write as a tick (heartbeat fallback for accumulator).

    Uses the short-lived WebSocket connection that already works for signal generation.
    Writes to price_snapshots with source='polymarket_ws_tick' for TWAP calculation.
    Called on every heartbeat as a reliable fallback when the persistent accumulator
    thread fails (e.g. on free hosting tiers that kill long-lived connections).

    Returns True if a tick was written, False on failure.
    """
    try:
        import asyncio
        from data_fetcher import get_polymarket_chainlink_price
        import db

        result = asyncio.get_event_loop().run_until_complete(
            get_polymarket_chainlink_price(timeout_s=4.0)
        )
        if result:
            price, ts = result
            ts_ms = ts if ts else int(time.time() * 1000)
            db.write_price_snapshot_sync(
                source="polymarket_ws_tick",
                symbol="BTCUSD",
                price=float(price),
                timestamp_ms=ts_ms,
            )
            log.info(f"Recorded Polymarket WS tick: ${price:,.2f}")
            return True
    except Exception as e:
        log.debug(f"Polymarket WS tick record failed (non-fatal): {e}")
    return False


def resolve_previous_hour(duration: str = "1h") -> bool:
    """5.3 + Phase 7.2c — Resolution checker (duration-aware).

    Called by heartbeat once a market window has closed.
    Fetches price data, determines outcome, writes to window_outcomes,
    and resolves any paper trades using the same outcome.
    Returns True if any window was resolved.

    Resolution method:
      - 1h: Binance candle open/close
      - 15m: Tick-based from price_snapshots table (Chainlink ticks).
             Falls back to Coinbase candles (get_price_at_time) if insufficient ticks.
    """
    import db
    from market_config import get_config

    config = get_config(duration)
    window_ms = config["window_ms"]

    client = db.get_client()

    # Find windows that need resolution (have observations, no outcome yet)
    try:
        unresolved_windows = db.get_unresolved_window_outcomes(client, duration)
    except Exception as e:
        log.warning(f"Failed to fetch unresolved window outcomes: {e}")
        return False

    # Filter out phantom windows (window_start=0)
    unresolved_windows = [w for w in unresolved_windows if w > 0]

    if not unresolved_windows:
        return False

    now_ms = int(time.time() * 1000)
    resolved_count = 0

    for window_start in unresolved_windows:
        close_ms = window_start + window_ms

        # Skip windows that haven't closed yet
        if close_ms > now_ms:
            continue

        try:
            # Determine outcome using the appropriate source
            if duration == "15m":
                outcome = _resolve_via_chainlink_ticks(client, window_start, close_ms)
            else:
                outcome = _resolve_via_binance(window_start, close_ms)

            if outcome is None:
                continue  # not enough data yet, retry next heartbeat

            # Validate against Polymarket's official resolution (best-effort)
            official_outcome = None
            try:
                sig_resp = (
                    client.table("signals")
                    .select("condition_id")
                    .eq("market_window_start", window_start)
                    .eq("market_duration", duration)
                    .not_.is_("condition_id", "null")
                    .limit(1)
                    .execute()
                )
                sig_rows = sig_resp.data or []
                if sig_rows:
                    cid = sig_rows[0].get("condition_id")
                    if cid:
                        from polymarket_fetcher import get_polymarket_resolution
                        pm = get_polymarket_resolution(cid)
                        if pm:
                            official_outcome = pm["outcome"]
                            if official_outcome != outcome:
                                log.warning(
                                    f"Resolution MISMATCH window={window_start} duration={duration}: "
                                    f"Verge={outcome} Polymarket={official_outcome}"
                                )
                            else:
                                log.info(
                                    f"Resolution AGREES window={window_start} duration={duration}: "
                                    f"{outcome} == {official_outcome}"
                                )
            except Exception as e:
                log.warning(f"Polymarket resolution check failed (non-fatal): {e}")

            # Write outcome to window_outcomes (single source of truth)
            db.write_window_outcome(client, duration, window_start, outcome,
                                    official_outcome=official_outcome)

            # If a paper trade exists for this window, resolve it with the same outcome
            trade = _get_paper_trade_for_window(client, duration, window_start)
            if trade and trade.get("resolved_outcome") is None:
                db.resolve_trade_with_outcome(
                    client,
                    signal_id=trade["signal_id"],
                    outcome=outcome,
                    odds=trade.get("odds", 0.50),
                    decision=trade.get("decision", ""),
                )

            resolved_count += 1

        except Exception as e:
            log.warning(f"Resolution failed for window {window_start} duration={duration}: {e}")

    log.info(f"Resolved {resolved_count}/{len(unresolved_windows)} windows (duration={duration})")
    return resolved_count > 0


def _resolve_via_binance(window_start: int, window_close: int) -> str | None:
    """Resolve outcome via Binance 1h candle open/close.

    Returns 'UP' or 'DOWN', or None if data unavailable.
    """
    from data_fetcher import get_binance_klines
    df = get_binance_klines(
        symbol="BTCUSDT",
        interval="1h",
        start_time=window_start,
        end_time=window_close,
        limit=1,
    )
    if df is None or len(df) == 0:
        return None
    open_price = float(df.iloc[0]["open"])
    close_price = float(df.iloc[0]["close"])
    outcome = "UP" if close_price > open_price else "DOWN"
    log.info(
        f"1h resolution via Binance: open=${open_price:,.2f} "
        f"close=${close_price:,.2f} -> {outcome}"
    )
    return outcome


def _resolve_via_chainlink_ticks(client, window_start: int, window_close: int) -> str | None:
    """Resolve outcome via Chainlink ticks from price_snapshots, with Coinbase and Binance fallbacks.

    Returns 'UP' or 'DOWN', or None if data unavailable.
    Resolution order: Chainlink ticks → Coinbase spot → Binance 5m candles.
    """
    # 1. Try Chainlink ticks
    try:
        ticks = db.get_price_snapshots(
            client, source="chainlink", symbol="BTC",
            since_ms=window_start, limit=1000,
        )
        window_ticks = [t for t in ticks if t["timestamp_ms"] <= window_close]

        if len(window_ticks) >= 2:
            open_price = float(window_ticks[0]["price"])
            close_price = float(window_ticks[-1]["price"])
            outcome = "UP" if close_price > open_price else "DOWN"
            log.info(
                f"15m resolution via ticks: open=${open_price:,.2f} "
                f"close=${close_price:,.2f} -> {outcome} "
                f"({len(window_ticks)} ticks)"
            )
            return outcome
    except Exception as e:
        log.warning(f"Chainlink tick resolution failed: {e}")

    # 2. Try Coinbase spot prices
    try:
        from data_fetcher import get_price_at_time
        open_price = get_price_at_time(window_start)
        close_price = get_price_at_time(window_close)
        if open_price and close_price:
            outcome = "UP" if close_price > open_price else "DOWN"
            log.info(
                f"15m resolution via Coinbase: open=${open_price:,.2f} "
                f"close=${close_price:,.2f} -> {outcome}"
            )
            return outcome
    except Exception as e:
        log.warning(f"Coinbase resolution failed: {e}")

    # 3. Try Binance 5m candles as final fallback
    try:
        from data_fetcher import get_binance_klines
        df = get_binance_klines(
            symbol="BTCUSDT",
            interval="5m",
            start_time=window_start,
            end_time=window_close,
            limit=20,
        )
        if df is not None and len(df) >= 2:
            open_price = float(df.iloc[0]["open"])
            close_price = float(df.iloc[-1]["close"])
            outcome = "UP" if close_price > open_price else "DOWN"
            log.info(
                f"15m resolution via Binance 5m: open=${open_price:,.2f} "
                f"close=${close_price:,.2f} -> {outcome} "
                f"({len(df)} candles)"
            )
            return outcome
    except Exception as e:
        log.warning(f"Binance 5m resolution failed: {e}")

    log.warning(
        f"All resolution methods failed for window {window_start}-{window_close}"
    )
    return None


def _get_paper_trade_for_window(client, duration: str, window_start: int) -> dict | None:
    """Get the paper trade for a specific window, if one exists."""
    result = (
        client.table("paper_trades")
        .select("signal_id, resolved_outcome, odds, decision")
        .eq("market_duration", duration)
        .eq("market_window_start", window_start)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


# ── TWAP: Time-Weighted Average Price (Phase 3) ──────────────────────

def compute_twap(ticks: list[dict], window_end_ms: int, window_seconds: int = 60) -> float | None:
    """Compute a time-weighted average price over a sliding window.

    ticks: [{'timestamp_ms': int, 'price': float}, ...], any order.
    Time-weights each price by how long it was the 'current' price within
    the window, correctly clipping the first tick's contribution if it
    started before the window began.
    """
    window_start_ms = window_end_ms - (window_seconds * 1000)
    ticks = sorted([t for t in ticks if t["timestamp_ms"] <= window_end_ms],
                    key=lambda t: t["timestamp_ms"])
    ticks = [t for t in ticks if t["timestamp_ms"] >= window_start_ms] or ticks[-1:]
    if not ticks:
        return None

    weighted_sum, total_weight = 0.0, 0.0
    for i, tick in enumerate(ticks):
        seg_start = max(tick["timestamp_ms"], window_start_ms)
        seg_end = ticks[i + 1]["timestamp_ms"] if i + 1 < len(ticks) else window_end_ms
        duration = max(0, seg_end - seg_start)
        weighted_sum += tick["price"] * duration
        total_weight += duration

    return weighted_sum / total_weight if total_weight > 0 else ticks[-1]["price"]


def start_ws_tick_accumulator() -> None:
    """Start a background thread that accumulates Polymarket WS ticks to price_snapshots.

    Follows the same pattern as telegram.start_bot_listener(): a daemon thread
    with automatic reconnect on failure. Writes every BTC tick to the
    price_snapshots table tagged source='polymarket_ws_tick'.
    """
    import threading

    def _run():
        while True:
            try:
                asyncio.run(_accumulate_ticks())
            except Exception as e:
                log.warning(f"WS tick accumulator dropped, reconnecting: {e}")
                time.sleep(5)

    thread = threading.Thread(target=_run, daemon=True, name="ws-tick-accumulator")
    thread.start()
    log.info("WS tick accumulator started")


async def _accumulate_ticks():
    """Persistent WebSocket connection that writes every BTC tick to the DB."""
    import asyncio
    import json
    import websockets
    import db

    url = "wss://ws-live-data.polymarket.com"
    async with websockets.connect(url, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "action": "subscribe",
            "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*", "filters": ""}],
        }))
        log.info("WS tick accumulator connected, listening for BTC ticks")
        async for raw in ws:
            data = json.loads(raw)
            if data.get("topic") != "crypto_prices_chainlink":
                continue
            payload = data.get("payload") or {}
            symbol = str(payload.get("symbol") or payload.get("pair") or "").lower()
            if "btc" not in symbol:
                continue
            price = payload.get("value") or payload.get("price")
            ts = payload.get("timestamp") or payload.get("updatedAt")
            if price is not None:
                ts_ms = int(float(ts) * 1000) if ts else int(time.time() * 1000)
                db.write_price_snapshot_sync(
                    source="polymarket_ws_tick",
                    symbol="BTCUSD",
                    price=float(price),
                    timestamp_ms=ts_ms,
                )
