"""Verge Backend API — Phase 4–5.

Flask app exposing:
  GET /api/signal   — live signal (4.4)
  GET /api/health   — health check (4.5)
  GET /api/heartbeat — heartbeat with persistence (6.1)
  GET /api/stats    — aggregate stats (5.4)
  GET /api/candles  — 5m candles for mini-chart
"""
import os
import sys
import logging
import time
from flask import Flask, jsonify, request
from flask_cors import CORS
from functools import wraps

sys.path.insert(0, os.path.dirname(__file__))

import db  # Force full import (supabase → realtime → websockets) before any threads start
import netrc  # requests lazily imports this; force load before threads start

from engine import generate_signal, persist_signal, resolve_previous_hour, retroactive_regrade_trades, record_price_tick
from polymarket_rtds import start_rtds_thread
from chainlink_ws import start_chainlink_ws_thread
from telegram import alert_on_signal, send_hourly_summary, start_bot_listener

app = Flask(__name__)

# Start Telegram bot listener for /start commands (once, primary worker only)
if os.environ.get("WEB_CONCURRENCY", "1") == "1":
    start_bot_listener()
    start_rtds_thread()
    start_chainlink_ws_thread()

# CORS: restrict to Vercel frontend in production, allow all in dev
VERCEL_URL = os.environ.get("VERCEL_URL", "")
cors_origins = [f"https://{VERCEL_URL}"] if VERCEL_URL else ["*"]
CORS(app, origins=cors_origins)

VERGE_SECRET = os.environ.get("VERGE_SECRET", "")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("verge.api")


def require_secret(f):
    """8.4 — Shared secret check on every /api/* route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not VERGE_SECRET:
            return f(*args, **kwargs)
        provided = request.headers.get("X-Secret") or request.args.get("secret")
        if provided != VERGE_SECRET:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/api/health")
@require_secret
def health():
    """4.5 — Health endpoint."""
    return jsonify({
        "status": "ok",
        "timestamp": int(time.time()),
        "service": "verge-backend",
    })


@app.route("/api/debug")
@require_secret
def debug():
    """Diagnostic endpoint — check why data isn't persisting."""
    import db
    results = {}

    # 1. Check Supabase connection
    try:
        client = db.get_client()
        results["supabase_connected"] = True
    except Exception as e:
        results["supabase_connected"] = False
        results["supabase_error"] = str(e)
        return jsonify(results)

    # 2. Count rows in each table
    for table in ["signals", "paper_trades", "odds_snapshots", "price_snapshots"]:
        try:
            resp = client.table(table).select("id", count="exact").execute()
            results[f"{table}_count"] = resp.count if hasattr(resp, 'count') else len(resp.data)
        except Exception as e:
            results[f"{table}_count"] = f"error: {e}"

    # 3. Check if market_duration column exists
    try:
        resp = client.table("signals").select("market_duration").limit(1).execute()
        results["market_duration_column"] = "exists"
    except Exception as e:
        results["market_duration_column"] = f"missing or error: {e}"

    # 4. Verify signals table is readable
    try:
        resp = client.table("signals").select("id").limit(1).execute()
        results["signals_readable"] = True
    except Exception as e:
        results["signals_readable"] = f"FAILED: {e}"

    # 5. Check VERGE_SECRET env var
    results["verge_secret_set"] = bool(os.environ.get("VERGE_SECRET"))

    return jsonify(results)


@app.route("/api/signal")
@require_secret
def signal():
    """Signal endpoint.

    Returns the full live signal as JSON.
    Accepts ?duration=1h (default) or ?duration=15m.
    """
    try:
        duration = request.args.get("duration", "1h")
        sig = generate_signal(duration=duration)
        return jsonify({
            "decision": sig.decision,
            "final_decision": sig.final_decision,
            "confidence": sig.confidence,
            "score": sig.score,
            "model_probability": sig.model_probability,
            "duration": sig.duration,
            "indicators": {
                "rsi": sig.rsi,
                "ma_signal": sig.ma_signal,
                "volume_signal": sig.volume_signal,
            },
            "market": {
                "token_id": sig.market_token_id,
                "slug": sig.market_slug,
                "hour_open_time": sig.hour_open_time,
                "hour_end_time": sig.hour_end_time,
                "minutes_remaining": sig.minutes_remaining,
                "seconds_remaining": sig.seconds_remaining,
            },
            "odds": sig.odds,
            "edge_pct": sig.edge_pct,
            "fee_eroded": sig.fee_eroded,
            "suggested_price": sig.suggested_price,
            "strike_price": sig.strike_price,
            "strike_source": sig.strike_source,
            "current_price": sig.current_price,
            "price_source": sig.price_source,
            "reference_status": sig.reference_status,
            "reference_age_ms": sig.reference_age_ms,
            "quality_status": sig.quality_status,
            "current_reference": sig.current_price,
            "current_reference_source": sig.price_source,
            "reference_quality": sig.quality_status,
            "fallback_used": sig.reference_status == "fallback",
            "reference_age_seconds": round(sig.reference_age_ms / 1000, 2) if sig.reference_age_ms else None,
            "difference": round(sig.current_price - sig.strike_price, 2) if sig.current_price and sig.strike_price else None,
            "difference_percent": round((sig.current_price - sig.strike_price) / sig.strike_price * 100, 6) if sig.current_price and sig.strike_price else None,
            "note": sig.note,
            "divergence_signal": sig.divergence_signal,
            "fear_greed_value": sig.fear_greed_value,
        })
    except Exception as e:
        log.exception("Signal generation failed")
        return jsonify({
            "error": str(e),
            "decision": "SKIP",
            "final_decision": "SKIP",
        }), 500


@app.route("/api/candles")
@require_secret
def candles():
    """Fetch candles for the mini-chart.

    Accepts ?duration=1h (default, 5m Binance bars) or ?duration=15m (1m Chainlink/Binance bars).
    Returns candle data + Polymarket's official strike price + spot price.
    """
    try:
        import time as _time
        from data_fetcher import get_spot_price
        from engine import get_current_market

        duration = request.args.get("duration", "1h")
        now_ms = int(_time.time() * 1000)

        # Get official strike from Polymarket
        market = get_current_market(duration)
        official_strike = market.get("price_to_beat") if market else None

        if duration == "15m":
            # 15m: return 1-minute bars from Chainlink/Binance
            from market_config import get_config
            config = get_config("15m")
            bars_lookback = config.get("bar_lookback", 20)

            from engine import get_current_price_data_for_duration
            df, _fetch_failed = get_current_price_data_for_duration(config)

            if df is None or len(df) == 0:
                return jsonify({"candles": [], "strike": None, "spot": None, "now_ms": now_ms, "duration": "15m"})

            df_recent = df.tail(bars_lookback)
            candles_list = []
            for _, row in df_recent.iterrows():
                candles_list.append({
                    "time": int(row["open_time"]),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                })

            # 15m strike: prefer official, then RTDS Chainlink TWAP at window start
            if official_strike:
                strike = round(official_strike, 2)
            elif market and market.get("window_open"):
                from polymarket_fetcher import get_15m_opening_reference
                strike_val, _ = get_15m_opening_reference(market["window_open"])
                strike = round(strike_val, 2) if strike_val else None
            else:
                strike = None
            spot = get_spot_price()

            return jsonify({
                "candles": candles_list,
                "strike": strike,
                "spot": round(spot, 2) if spot else None,
                "now_ms": now_ms,
                "duration": "15m",
            })
        else:
            # 1h: return 5-minute Binance candles (existing behavior)
            from data_fetcher import get_price_with_fallback

            start_ms = now_ms - (16 * 5 * 60 * 1000)

            df = get_price_with_fallback(
                symbol="BTCUSDT",
                interval="5m",
                start_time=start_ms,
                end_time=now_ms,
            )

            if df is None or len(df) == 0:
                return jsonify({"candles": [], "strike": None, "spot": None, "now_ms": now_ms, "duration": "1h"})

            df_hour = df.tail(12)
            candles_list = []
            for _, row in df_hour.iterrows():
                candles_list.append({
                    "time": int(row["open_time"]),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                })

            strike = round(official_strike, 2) if official_strike else round(float(df_hour.iloc[0]["open"]), 2)
            spot = get_spot_price()

            return jsonify({
                "candles": candles_list,
                "strike": strike,
                "spot": round(spot, 2) if spot else None,
                "now_ms": now_ms,
                "duration": "1h",
            })
    except Exception as e:
        log.warning(f"Candles fetch failed: {e}")
        return jsonify({"candles": [], "strike": None, "spot": None, "now_ms": int(time.time() * 1000)})


@app.route("/api/spot")
@require_secret
def spot():
    """Ultra-lightweight spot price endpoint for fast polling.
    Returns the latest RTDS Chainlink tick (same feed Polymarket shows).
    """
    try:
        now_ms = int(time.time() * 1000)
        from polymarket_rtds import get_rtds_ticks
        ticks = get_rtds_ticks(since_ms=now_ms - 30_000)
        if ticks:
            latest = ticks[-1]
            return jsonify({
                "spot": round(latest["price"], 2),
                "source": "rtds_chainlink",
                "age_ms": now_ms - latest["timestamp_ms"],
                "now_ms": now_ms,
            })
        # Fallback: Chainlink WSS
        from chainlink_ws import get_chainlink_ws_price
        wss_price = get_chainlink_ws_price()
        if wss_price and wss_price > 0:
            return jsonify({"spot": round(wss_price, 2), "source": "chainlink_ws", "now_ms": now_ms})
        # Fallback: Coinbase
        from data_fetcher import get_spot_price
        price = get_spot_price()
        return jsonify({"spot": round(price, 2) if price else None, "source": "coinbase", "now_ms": now_ms})
    except Exception as e:
        return jsonify({"spot": None, "now_ms": int(time.time() * 1000)})


@app.route("/api/heartbeat")
@require_secret
def heartbeat():
    """6.1 — Heartbeat endpoint.

    Generates signal for each duration (1h + 15m), persists to Supabase,
    resolves previous windows. Idempotent: won't duplicate signals.

    Frozen durations skip signal generation but still resolve open trades.
    """
    try:
        from market_config import supported_durations

        client = db.get_client()
        frozen = db.get_frozen_durations(client)

        # Daily observation cleanup (once per day)
        try:
            db.cleanup_old_observations(client, max_age_days=30)
        except Exception as e:
            log.warning(f"Observation cleanup failed (non-fatal): {e}")

        results = []
        for dur in supported_durations():
            try:
                # Frozen durations: skip generation, still resolve
                if dur in frozen:
                    log.info(f"[{dur}] Frozen — skipping signal generation")
                    try:
                        resolve_previous_hour(duration=dur)
                    except Exception as e:
                        log.warning(f"Resolution check failed for {dur} (non-fatal): {e}")
                    results.append({"duration": dur, "status": "frozen"})
                    continue

                # Generate signal
                sig = generate_signal(duration=dur)

                # Log window observation (always, no idempotency gate)
                try:
                    obs = db.WindowObservationRow(
                        market_duration=sig.duration,
                        market_window_start=sig.hour_open_time or 0,
                        seconds_into_window=max(0, int(time.time() * 1000 - (sig.hour_open_time or 0)) // 1000),
                        odds=sig.odds,
                        current_price=sig.current_price,
                        strike_price=sig.strike_price,
                        rsi=sig.rsi,
                        ma_signal=sig.ma_signal,
                        volume_signal=sig.volume_signal,
                        divergence_signal=sig.divergence_signal,
                        fear_greed_value=sig.fear_greed_value,
                        score=sig.score,
                        hypothetical_decision=sig.final_decision,
                    )
                    db.log_window_observation(client, obs)
                except Exception as e:
                    log.warning(f"Observation log failed for {dur} (non-fatal): {e}")

                # Persist signal
                try:
                    sig_id, persist_status = persist_signal(sig)
                except Exception as e:
                    sig_id = None
                    persist_status = "error"
                    log.warning(f"Persist failed for {dur} (non-fatal): {e}")

                # Record Chainlink price tick for 15m bar-building
                if dur == "15m":
                    try:
                        record_price_tick(duration=dur)
                    except Exception as e:
                        log.warning(f"Price tick recording failed for {dur} (non-fatal): {e}")

                # Telegram alert (only on new signal or persist failure)
                if persist_status == "duplicate":
                    log.info(f"Skipping duplicate alert for {dur} window {sig.hour_open_time}")
                else:
                    try:
                        alert_on_signal(sig, signal_id=sig_id, unlogged=(persist_status == "error"))
                    except Exception as e:
                        log.warning(f"Telegram alert failed for {dur} (non-fatal): {e}")

                # Try to resolve previous window's trades
                resolved_info = None
                try:
                    resolved = resolve_previous_hour(duration=dur)
                    resolved_info = resolved
                except Exception as e:
                    log.warning(f"Resolution check failed for {dur} (non-fatal): {e}")

                results.append({
                    "duration": dur,
                    "decision": sig.final_decision,
                    "market": sig.market_slug,
                    "minutes_remaining": sig.minutes_remaining,
                    "resolved": resolved_info,
                })
            except Exception as e:
                log.warning(f"Heartbeat failed for {dur}: {e}")
                results.append({"duration": dur, "error": str(e)})

        # Retroactive regrade: fix any trades graded against Verge's outcome
        # when Polymarket's official_outcome differs (idempotent, no-op after first run)
        try:
            retroactive_regrade_trades()
        except Exception as e:
            log.warning(f"Retroactive regrade failed (non-fatal): {e}")

        # Hourly 15m summary (sent at top of hour, non-fatal)
        try:
            send_hourly_summary(client)
        except Exception as e:
            log.warning(f"Hourly summary failed (non-fatal): {e}")

        return jsonify({
            "status": "ok",
            "timestamp": int(time.time()),
            "markets": results,
        })
    except Exception as e:
        log.exception("Heartbeat failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/cron")
@require_secret
def cron_tick():
    """Lightweight cron endpoint — same work as heartbeat, minimal response.

    Use this for cron-job.org instead of /api/heartbeat to avoid
    response size limits. Returns only status + timestamp.
    """
    try:
        from market_config import supported_durations

        client = db.get_client()
        frozen = db.get_frozen_durations(client)

        try:
            db.cleanup_old_observations(client, max_age_days=30)
        except Exception:
            pass

        for dur in supported_durations():
            try:
                if dur in frozen:
                    resolve_previous_hour(duration=dur)
                    continue

                sig = generate_signal(duration=dur)

                try:
                    obs = db.WindowObservationRow(
                        market_duration=sig.duration,
                        market_window_start=sig.hour_open_time or 0,
                        seconds_into_window=max(0, int(time.time() * 1000 - (sig.hour_open_time or 0)) // 1000),
                        odds=sig.odds,
                        current_price=sig.current_price,
                        strike_price=sig.strike_price,
                        rsi=sig.rsi,
                        ma_signal=sig.ma_signal,
                        volume_signal=sig.volume_signal,
                        divergence_signal=sig.divergence_signal,
                        fear_greed_value=sig.fear_greed_value,
                        score=sig.score,
                        hypothetical_decision=sig.final_decision,
                    )
                    db.log_window_observation(client, obs)
                except Exception:
                    pass

                try:
                    sig_id, persist_status = persist_signal(sig)
                except Exception:
                    persist_status = "error"

                if dur == "15m":
                    try:
                        record_price_tick(duration=dur)
                    except Exception:
                        pass

                if persist_status != "duplicate":
                    try:
                        alert_on_signal(sig, signal_id=sig_id, unlogged=(persist_status == "error"))
                    except Exception:
                        pass

                try:
                    resolve_previous_hour(duration=dur)
                except Exception:
                    pass

            except Exception:
                pass

        try:
            send_hourly_summary(client)
        except Exception:
            pass

        return jsonify({"ok": 1})
    except Exception:
        return jsonify({"ok": 0})


@app.route("/api/stats")
@require_secret
def stats():
    """Stats endpoint.

    Aggregate stats over paper_trades.
    Optional ?duration=1h|15m filter.
    """
    try:
        import db
        duration = request.args.get("duration")
        client = db.get_client()
        result = db.get_stats(client, duration=duration)
        # Add rolling-window stats (last 30 resolved trades)
        rolling = db.get_rolling_stats(client, duration=duration)
        result.update(rolling)
        return jsonify(result)
    except Exception as e:
        log.warning(f"Stats query failed (returning defaults): {e}")
        return jsonify({
            "total_trades": 0,
            "resolved": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "cumulative_pnl": 0,
            "unlock_real_orders": False,
            "recent_trades": [],
            "recent_signals": [],
        })


@app.route("/api/weekly-digest")
@require_secret
def weekly_digest():
    """Weekly digest with performance stats.

    Sends a Telegram message with current standing.
    Should be triggered weekly via cron or manual call.
    """
    try:
        import db
        from telegram import send_weekly_digest

        client = db.get_client()
        stats = db.get_stats(client)

        sent = send_weekly_digest(stats)
        return jsonify({
            "status": "ok",
            "sent": sent,
            "stats": stats,
        })
    except Exception as e:
        log.exception("Weekly digest failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/performance")
@require_secret
def performance():
    """Rolling performance summary over last 200 signals, or a specific batch."""
    try:
        import db
        duration = request.args.get("duration")
        batch_offset = request.args.get("batch_offset", type=int)
        batch_count = request.args.get("batch_count", type=int)
        client = db.get_client()
        result = db.get_performance_summary(
            client, duration=duration,
            batch_offset=batch_offset, batch_count=batch_count,
        )
        return jsonify(result)
    except Exception as e:
        log.warning(f"Performance query failed: {e}")
        return jsonify({
            "total_signals": 0, "window": 200, "profitable": 0, "resolved": 0, "roi_pct": 0.0,
        })


@app.route("/api/signal-log")
@require_secret
def signal_log():
    """Paginated signal log with resolution data."""
    try:
        import db
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 25))
        duration = request.args.get("duration")
        client = db.get_client()
        result = db.get_paginated_signals(client, offset=offset, limit=limit, duration=duration)
        return jsonify(result)
    except Exception as e:
        log.warning(f"Signal log query failed: {e}")
        return jsonify({"signals": [], "total": 0})


@app.route("/api/signal-log/<int:signal_id>")
@require_secret
def signal_log_detail(signal_id):
    """Fetch a single signal by ID for deep-linking."""
    try:
        import db
        client = db.get_client()
        resp = client.table("signals").select(
            "id, final_decision, market_window_start, timestamp, score, market_duration, "
            "strike_price, current_price, odds, edge_pct, rsi, ma_signal, volume_signal, "
            "note, divergence_signal, fear_greed_value"
        ).eq("id", signal_id).execute()
        if not resp.data:
            return jsonify({"error": "Signal not found"}), 404
        signal = resp.data[0]
        pt = client.table("paper_trades").select(
            "resolved_outcome, simulated_pnl, decision"
        ).eq("signal_id", signal_id).execute()
        if pt.data:
            signal["resolved_outcome"] = pt.data[0].get("resolved_outcome")
            signal["simulated_pnl"] = pt.data[0].get("simulated_pnl")
            signal["decision"] = pt.data[0].get("decision")
        return jsonify(signal)
    except Exception as e:
        log.warning(f"Signal detail query failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/signal-log/batches")
@require_secret
def signal_log_batches():
    """Batch summaries for the signal log — groups signals into batches of 200."""
    try:
        import db
        duration = request.args.get("duration")
        client = db.get_client()
        result = db.get_batch_summaries(client, duration=duration)
        return jsonify(result)
    except Exception as e:
        log.warning(f"Batch summaries query failed: {e}")
        return jsonify({"batches": [], "total_batches": 0})


@app.route("/api/admin/freeze", methods=["POST"])
@require_secret
def freeze_duration():
    """Freeze or unfreeze a duration. Frozen durations skip signal generation."""
    try:
        from market_config import MARKET_CONFIGS
        duration = request.args.get("duration")
        frozen = request.args.get("frozen", "true").lower() == "true"
        if duration not in MARKET_CONFIGS:
            return jsonify({"error": "unknown duration"}), 400
        client = db.get_client()
        db.set_duration_frozen(client, duration, frozen)
        return jsonify({"duration": duration, "frozen": frozen})
    except Exception as e:
        log.warning(f"Freeze toggle failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug/resolve-status")
@require_secret
def debug_resolve_status():
    """Debug: check resolution health for all durations."""
    import db
    from market_config import supported_durations

    client = db.get_client()
    now_ms = int(time.time() * 1000)
    status = {}

    for dur in supported_durations():
        from market_config import get_config
        config = get_config(dur)
        window_ms = config["window_ms"]

        unresolved = db.get_unresolved_window_outcomes(client, dur)
        unresolved = [w for w in unresolved if w > 0]
        closed = [w for w in unresolved if w + window_ms <= now_ms]

        status[dur] = {
            "unresolved": len(unresolved),
            "closed_needing_resolve": len(closed),
        }

    return jsonify(status)


@app.route("/api/frozen")
@require_secret
def get_frozen():
    """Return currently frozen durations."""
    try:
        client = db.get_client()
        frozen = db.get_frozen_durations(client)
        return jsonify({"frozen": sorted(frozen)})
    except Exception as e:
        return jsonify({"frozen": []})


@app.route("/api/phase2-progress")
@require_secret
def phase2_progress():
    """Phase 2 readiness: count resolved 15m windows with official_outcome + notify when target hit."""
    try:
        import db
        client = db.get_client()
        target = 300

        outcomes = (
            client.table("window_outcomes")
            .select("market_window_start", count="exact")
            .eq("market_duration", "15m")
            .not_.is_("official_outcome", "null")
            .limit(5000)
            .execute()
        )
        count = outcomes.count if hasattr(outcomes, 'count') else len(outcomes.data)

        # Send Telegram notification once when target is reached
        if count >= target:
            already_notified = db.get_setting(client, "phase2_target_reached")
            if not already_notified:
                try:
                    from telegram import send_telegram_alert
                    send_telegram_alert(
                        f"Phase 2 data target reached!\n"
                        f"{count}/300 resolved 15m windows with Polymarket official outcome.\n"
                        f"Ready to run capture-point comparison."
                    )
                    db.set_setting(client, "phase2_target_reached", str(int(time.time())))
                except Exception as e:
                    log.warning(f"Phase 2 Telegram notification failed: {e}")

        return jsonify({
            "resolved": count,
            "target": target,
        })
    except Exception as e:
        log.warning(f"phase2-progress failed: {e}")
        return jsonify({"resolved": 0, "target": 300})


@app.route("/api/window-observations")
@require_secret
def window_observations():
    """Return observation timeline for a window."""
    try:
        import db
        duration = request.args.get("duration", "15m")
        window_start = request.args.get("window_start", type=int)
        client = db.get_client()
        query = client.table("window_observations").select("*").eq("market_duration", duration)
        if window_start:
            query = query.eq("market_window_start", window_start)
        else:
            latest = client.table("window_observations").select("market_window_start").eq("market_duration", duration).order("market_window_start", desc=True).limit(1).execute()
            if latest.data:
                window_start = latest.data[0]["market_window_start"]
                query = client.table("window_observations").select("*").eq("market_duration", duration).eq("market_window_start", window_start)
        result = query.order("seconds_into_window").execute()
        return jsonify(result.data)
    except Exception as e:
        log.warning(f"window_observations failed: {e}")
        return jsonify([])


@app.route("/api/window-observations/recent")
@require_secret
def window_observations_recent():
    """List recent windows with observation data."""
    try:
        import db
        duration = request.args.get("duration", "15m")
        limit = request.args.get("limit", 20, type=int)
        client = db.get_client()
        windows = db.get_distinct_observation_windows(client, duration, limit=limit)
        return jsonify(windows)
    except Exception as e:
        log.warning(f"window_observations_recent failed: {e}")
        return jsonify([])


@app.route("/api/window-outcomes/recent")
@require_secret
def window_outcomes_recent():
    """Return recent windows with outcomes, observation counts, and trade info.

    Used by the Timeline page to show readiness status per window.
    """
    try:
        import db
        duration = request.args.get("duration", "15m")
        limit = request.args.get("limit", 20, type=int)
        client = db.get_client()
        result = db.get_window_outcomes_with_observations(client, duration, limit=limit)
        return jsonify(result)
    except Exception as e:
        log.warning(f"window_outcomes_recent failed: {e}")
        return jsonify([])


@app.route("/api/diagnostics", methods=["GET"])
@require_secret
def api_diagnostics():
    """Diagnostics endpoint: source breakdown, live prices, reference audit, and resolution health."""
    import time as _time
    import db
    from engine import compute_twap
    from chainlink_fetcher import get_chainlink_price
    from pyth_fetcher import get_pyth_btc_price_value
    from data_fetcher import get_spot_price
    from polymarket_fetcher import get_polymarket_live_market
    from price_reference import assess_observation_health, build_reference_audit, compare_prices

    now_ms = int(_time.time() * 1000)
    client = db.get_client()

    source_stats = db.get_source_breakdown(client, since_ms=now_ms - 86_400_000)

    twap_ticks = db.get_recent_price_snapshots(
        client, "rtds_chainlink", "BTCUSD", since_ms=now_ms - 90_000
    )
    from polymarket_rtds import get_rtds_health, get_rtds_ticks
    rtds_ticks = get_rtds_ticks(since_ms=now_ms - 90_000)
    rtds_health_detail = assess_observation_health(rtds_ticks, now_ms=now_ms)
    latest_rtds = rtds_ticks[-1]["price"] if rtds_ticks else None

    from chainlink_ws import get_chainlink_ws_health, get_chainlink_ws_price
    live = {
        "polymarket_rtds_chainlink": latest_rtds,
        "polymarket_rtds_60s_twap_estimate": compute_twap(twap_ticks, now_ms) if len(twap_ticks) >= 2 else None,
        "chainlink_ws": get_chainlink_ws_price(),
        "chainlink_onchain": get_chainlink_price(),
        "pyth": get_pyth_btc_price_value(),
        "coinbase_spot": get_spot_price(),
    }

    rtds_health = get_rtds_health()
    live["rtds_tick_count_5m"] = len(db.get_recent_price_snapshots(
        client, "rtds_chainlink", "BTCUSD", since_ms=now_ms - 300_000
    ))
    live["rtds_last_tick_age_ms"] = rtds_health["last_tick_age_ms"]
    live["rtds_quality"] = rtds_health_detail.to_dict()
    live["rtds_connection_health"] = rtds_health

    ws_health = get_chainlink_ws_health()
    live["chainlink_ws_age_ms"] = ws_health["last_tick_age_ms"]

    from chainlink_fetcher import get_rpc_health
    rpc_health = get_rpc_health()

    recent_signals = db.get_recent_signals_with_source(client, limit=20)
    accuracy = db.get_resolution_accuracy_by_source(client)
    resolution_agreement = db.get_resolution_agreement(client)
    resolution_audit = db.get_resolution_audit_summary(client)
    strike_source_dist = db.get_strike_source_distribution(client)

    last_tick = twap_ticks[-1].price if twap_ticks else None
    twap_val = live["polymarket_rtds_60s_twap_estimate"]
    twap_vs_tick = {
        "twap": twap_val,
        "last_single_tick": last_tick,
        "difference_pct": round(abs(twap_val - last_tick) / last_tick * 100, 4)
            if twap_val and last_tick else None,
    }

    pm_15m = get_polymarket_live_market("btc-up-or-down-15m")
    pm_1h = get_polymarket_live_market("btc-up-or-down-hourly")

    verge_reference = (
        live.get("polymarket_rtds_60s_twap_estimate")
        or live.get("chainlink_onchain")
        or live.get("pyth")
        or live.get("coinbase_spot")
    )
    if live.get("polymarket_rtds_60s_twap_estimate"):
        verge_reference_source = "polymarket_rtds_60s_twap_estimate"
    elif live.get("chainlink_onchain"):
        verge_reference_source = "chainlink_onchain"
    elif live.get("pyth"):
        verge_reference_source = "pyth"
    elif live.get("coinbase_spot"):
        verge_reference_source = "coinbase_spot"
    else:
        verge_reference_source = None

    def _enrich_pm(pm_data):
        if not pm_data:
            return pm_data
        strike = pm_data.get("price_to_beat")
        strike_source = pm_data.get("price_to_beat_source") or ("polymarket_price_to_beat" if strike else None)
        return {
            **pm_data,
            "price_to_beat_source": strike_source,
            "strike_comparison": compare_prices(strike, strike),
            "current_reference_comparison": compare_prices(verge_reference, strike),
            "verge_current_reference": verge_reference,
            "verge_current_reference_source": verge_reference_source,
        }

    polymarket_live = {
        "15m": _enrich_pm(pm_15m),
        "1h": _enrich_pm(pm_1h),
    }

    reference_audit_15m = build_reference_audit(
        market_id=pm_15m.get("condition_id") if pm_15m else None,
        window_start=None,
        window_end=None,
        price_to_beat=pm_15m.get("price_to_beat") if pm_15m else None,
        price_to_beat_source=pm_15m.get("price_to_beat_source") if pm_15m else None,
        current_reference=verge_reference,
        current_reference_source=verge_reference_source,
        reference_health=rtds_health_detail,
        opening_reference=pm_15m.get("price_to_beat") if pm_15m else None,
        opening_reference_source=pm_15m.get("price_to_beat_source") if pm_15m else None,
    )

    return jsonify({
        "source_breakdown": source_stats,
        "live_prices": live,
        "recent_signals": recent_signals,
        "resolution_accuracy": accuracy,
        "resolution_agreement": resolution_agreement,
        "resolution_audit": resolution_audit,
        "strike_source_distribution": strike_source_dist,
        "twap_vs_tick": twap_vs_tick,
        "polymarket_live": polymarket_live,
        "reference_audit_15m": reference_audit_15m,
        "source_comparison": _source_comparison(client, now_ms),
        "rpc_health": rpc_health,
        "timestamp": now_ms,
    })


@app.route("/api/price-reference", methods=["GET"])
@require_secret
def api_price_reference():
    """15m price-reference audit endpoint."""
    try:
        import time as _time
        import db
        from engine import compute_twap, get_current_market
        from polymarket_rtds import get_rtds_ticks
        from price_reference import assess_observation_health, build_reference_audit

        duration = request.args.get("duration", "15m")
        if duration != "15m":
            return jsonify({"error": "price-reference audit is currently scoped to 15m"}), 400

        now_ms = int(_time.time() * 1000)
        market = get_current_market("15m")
        rtds_ticks = get_rtds_ticks(since_ms=now_ms - 90_000)
        health = assess_observation_health(rtds_ticks, now_ms=now_ms)
        latest = rtds_ticks[-1] if rtds_ticks else None

        client = db.get_client()
        persisted_ticks = db.get_recent_price_snapshots(
            client, "rtds_chainlink", "BTCUSD", since_ms=now_ms - 90_000
        )
        twap = compute_twap(persisted_ticks, now_ms) if len(persisted_ticks) >= 2 else None

        strike = market.get("price_to_beat") if market else None
        strike_value = float(strike) if strike is not None else None
        strike_source = market.get("price_to_beat_source") if market else None
        audit = build_reference_audit(
            market_id=(market.get("market_id") or market.get("condition_id")) if market else None,
            window_start=market.get("window_open") if market else None,
            window_end=market.get("window_end") if market else None,
            price_to_beat=strike_value,
            price_to_beat_source=strike_source,
            current_reference=latest.get("price") if latest else None,
            current_reference_source="rtds_chainlink" if latest else None,
            reference_health=health,
            opening_reference=strike_value,
            opening_reference_source=strike_source,
        )
        return jsonify({
            "market": market,
            "reference": {
                "source": "rtds_chainlink" if latest else None,
                "price": latest.get("price") if latest else None,
                "observed_at_ms": latest.get("timestamp_ms") if latest else None,
            },
            "twap_estimate": {
                "window_seconds": 60,
                "value": twap,
                "source": "polymarket_rtds_60s_twap_estimate",
                "quality": health.status,
            },
            "audit": audit,
            "timestamp": now_ms,
        })
    except Exception as e:
        log.exception("price-reference audit failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/diagnostics/reference/<market_id>")
@require_secret
def api_diagnostics_reference(market_id):
    """Per-market reference audit: inspect strike, current reference, and health for a specific market."""
    try:
        import time as _time
        import db
        from engine import compute_twap, get_current_market
        from polymarket_rtds import get_rtds_ticks
        from price_reference import assess_observation_health, build_reference_audit

        now_ms = int(_time.time() * 1000)
        client = db.get_client()

        # Try to fetch market data from Gamma API by condition_id
        market_data = None
        try:
            resp = __import__("requests").get(
                f"https://gamma-api.polymarket.com/markets/{market_id}",
                timeout=10,
            )
            if resp.ok:
                market_data = resp.json()
        except Exception:
            pass

        if not market_data:
            # Try querying by condition_ids
            try:
                resp = __import__("requests").get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"condition_ids": market_id},
                    timeout=10,
                )
                if resp.ok:
                    result = resp.json()
                    if isinstance(result, list) and result:
                        market_data = result[0]
            except Exception:
                pass

        if not market_data:
            return jsonify({"error": f"Market {market_id} not found"}), 404

        # Extract strike from eventMetadata or market data
        from datetime import datetime
        event_start = market_data.get("eventStartTime") or market_data.get("startDate")
        event_end = market_data.get("endDate")
        window_start = None
        window_end = None
        if event_start:
            try:
                start_dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
                window_start = int(start_dt.timestamp() * 1000)
            except (ValueError, TypeError):
                pass
        if event_end:
            try:
                end_dt = datetime.fromisoformat(event_end.replace("Z", "+00:00"))
                window_end = int(end_dt.timestamp() * 1000)
            except (ValueError, TypeError):
                pass

        # Get strike price
        outcome_prices_raw = market_data.get("outcomePrices")
        up_odds = None
        if outcome_prices_raw:
            try:
                import json as _json
                prices = _json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                if prices:
                    up_odds = float(prices[0])
            except Exception:
                pass

        strike_price = None
        strike_source = None
        # Check the parent event for priceToBeat
        event_id = market_data.get("eventId")
        if event_id:
            try:
                evt_resp = __import__("requests").get(
                    f"https://gamma-api.polymarket.com/events/{event_id}",
                    timeout=10,
                )
                if evt_resp.ok:
                    evt = evt_resp.json()
                    metadata = evt.get("eventMetadata") or {}
                    ptb = metadata.get("priceToBeat")
                    if ptb:
                        try:
                            strike_price = float(ptb)
                            if strike_price > 0:
                                strike_source = "polymarket_price_to_beat"
                            else:
                                strike_price = None
                        except (TypeError, ValueError):
                            strike_price = None
            except Exception:
                pass

        # Get RTDS ticks and health
        rtds_ticks = get_rtds_ticks(since_ms=now_ms - 90_000)
        health = assess_observation_health(rtds_ticks, now_ms=now_ms)
        latest = rtds_ticks[-1] if rtds_ticks else None

        current_reference = latest["price"] if latest else None
        current_reference_source = "rtds_chainlink" if latest else None

        # TWAP estimate
        persisted_ticks = db.get_recent_price_snapshots(
            client, "rtds_chainlink", "BTCUSD", since_ms=now_ms - 90_000
        )
        twap = compute_twap(persisted_ticks, now_ms) if len(persisted_ticks) >= 2 else None

        audit = build_reference_audit(
            market_id=market_id,
            window_start=window_start,
            window_end=window_end,
            price_to_beat=strike_price,
            price_to_beat_source=strike_source,
            current_reference=current_reference,
            current_reference_source=current_reference_source,
            reference_health=health,
            opening_reference=strike_price,
            opening_reference_source=strike_source,
        )

        return jsonify({
            "market_id": market_id,
            "window_start": window_start,
            "window_end": window_end,
            "price_to_beat": strike_price,
            "price_to_beat_source": strike_source,
            "current_reference": current_reference,
            "current_reference_source": current_reference_source,
            "reference_quality": health.status if health else None,
            "twap_estimate": twap,
            "up_odds": up_odds,
            "question": market_data.get("question", ""),
            "audit": audit,
            "timestamp": now_ms,
        })
    except Exception as e:
        log.exception("diagnostics/reference audit failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/diagnostics/resolution-audit")
@require_secret
def api_resolution_audit():
    """Historical resolution audit: per-row data and aggregate statistics for 15m validation.

    Query params:
        duration — filter by "15m" or "1h" (default: all)
        limit    — max rows (default: 100, max: 1000)
        offset   — pagination offset (default: 0)
        stats    — if "true", include aggregate statistics
    """
    try:
        import db
        duration = request.args.get("duration")
        limit = min(int(request.args.get("limit", 100)), 1000)
        offset = int(request.args.get("offset", 0))
        include_stats = request.args.get("stats", "true").lower() == "true"

        client = db.get_client()
        rows = db.get_resolution_audit_rows(
            client, duration=duration, limit=limit, offset=offset,
        )
        stats = db.get_resolution_audit_statistics(client, duration=duration) if include_stats else None

        return jsonify({
            "rows": rows,
            "count": len(rows),
            "offset": offset,
            "limit": limit,
            "duration_filter": duration,
            "statistics": stats,
        })
    except Exception as e:
        log.exception("resolution-audit query failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/diagnostics/15m-reference")
@require_secret
def api_15m_reference():
    """Three-way comparison for 15m strike: Gamma priceToBeat vs RTDS TWAP vs market identity.

    Returns the exact market selected, its metadata, and both price sources
    so the discrepancy can be pinpointed.
    """
    try:
        import time as _time
        import db
        from engine import _get_current_15m_market, compute_twap
        from db import PriceSnapshotRow
        from polymarket_rtds import get_rtds_ticks
        from price_reference import assess_observation_health

        now_ms = int(_time.time() * 1000)
        client = db.get_client()

        # 1. Get exact market via deterministic slug
        market = _get_current_15m_market()

        if not market:
            return jsonify({"error": "No active 15m market found"}), 404

        window_start = market["window_open"]
        window_end = market["window_end"]
        slug = market["slug"]
        market_id = market.get("market_id")
        condition_id = market.get("condition_id")

        # 2. Gamma priceToBeat
        gamma_ptb = market.get("price_to_beat")
        gamma_ptb_source = market.get("price_to_beat_source")

        # 3. RTDS opening 60s TWAP from ring buffer
        rtds_ticks_buf = get_rtds_ticks(since_ms=window_start - 65_000)
        rtds_twap = None
        rtds_samples = 0
        rtds_first_ts = None
        rtds_last_ts = None
        rtds_largest_gap_ms = None

        if rtds_ticks_buf:
            rows = [PriceSnapshotRow(source="rtds_chainlink", symbol="BTCUSD",
                                     price=t["price"], timestamp_ms=t["timestamp_ms"])
                    for t in rtds_ticks_buf]
            window_rows = [r for r in rows
                           if window_start - 60_000 <= r.timestamp_ms <= window_start]
            if len(window_rows) >= 2:
                rtds_twap = compute_twap(window_rows, window_end_ms=window_start, window_seconds=60)
                rtds_samples = len(window_rows)
                rtds_first_ts = window_rows[0].timestamp_ms
                rtds_last_ts = window_rows[-1].timestamp_ms
                gaps = [window_rows[i].timestamp_ms - window_rows[i-1].timestamp_ms
                        for i in range(1, len(window_rows))]
                rtds_largest_gap_ms = max(gaps) if gaps else 0

        # 4. RTDS opening TWAP from DB (if ring buffer empty)
        rtds_twap_db = None
        rtds_samples_db = 0
        rtds_first_ts_db = None
        rtds_last_ts_db = None
        rtds_largest_gap_ms_db = None

        try:
            db_ticks = db.get_price_snapshots(
                client, source="rtds_chainlink", symbol="BTCUSD",
                since_ms=window_start - 65_000, limit=120,
            )
            db_window = [t for t in db_ticks
                         if window_start - 60_000 <= t["timestamp_ms"] <= window_start]
            if len(db_window) >= 2:
                db_rows = [PriceSnapshotRow(source="rtds_chainlink", symbol="BTCUSD",
                                            price=t["price"], timestamp_ms=t["timestamp_ms"])
                           for t in db_window]
                rtds_twap_db = compute_twap(db_rows, window_end_ms=window_start, window_seconds=60)
                rtds_samples_db = len(db_window)
                rtds_first_ts_db = db_window[0]["timestamp_ms"]
                rtds_last_ts_db = db_window[-1]["timestamp_ms"]
                gaps = [db_window[i]["timestamp_ms"] - db_window[i-1]["timestamp_ms"]
                        for i in range(1, len(db_window))]
                rtds_largest_gap_ms_db = max(gaps) if gaps else 0
        except Exception:
            pass

        # 5. RTDS health
        health = assess_observation_health(rtds_ticks_buf, now_ms=now_ms)

        # 6. Comparison
        best_twap = rtds_twap or rtds_twap_db
        best_source = "rtds_ring_buffer" if rtds_twap else ("rtds_db" if rtds_twap_db else None)

        difference = None
        difference_bps = None
        if gamma_ptb and best_twap:
            try:
                gamma_val = float(gamma_ptb)
                difference = round(gamma_val - best_twap, 2)
                difference_bps = round(abs(difference) / best_twap * 10_000, 2) if best_twap else None
            except (TypeError, ValueError):
                pass

        return jsonify({
            "market": {
                "slug": slug,
                "market_id": market_id,
                "condition_id": condition_id,
                "window_start": window_start,
                "window_end": window_end,
                "question": market.get("question", ""),
            },
            "gamma": {
                "price_to_beat": gamma_ptb,
                "source": gamma_ptb_source,
            },
            "rtds_ring_buffer": {
                "twap_60s": rtds_twap,
                "samples": rtds_samples,
                "first_timestamp": rtds_first_ts,
                "last_timestamp": rtds_last_ts,
                "largest_gap_ms": rtds_largest_gap_ms,
            },
            "rtds_db": {
                "twap_60s": rtds_twap_db,
                "samples": rtds_samples_db,
                "first_timestamp": rtds_first_ts_db,
                "last_timestamp": rtds_last_ts_db,
                "largest_gap_ms": rtds_largest_gap_ms_db,
            },
            "best_twap": {
                "value": best_twap,
                "source": best_source,
            },
            "comparison": {
                "gamma_price_to_beat": gamma_ptb,
                "best_twap": best_twap,
                "difference": difference,
                "difference_bps": difference_bps,
            },
            "rtds_health": health.to_dict() if health else None,
            "timestamp": now_ms,
        })
    except Exception as e:
        log.exception("15m-reference diagnostic failed")
        return jsonify({"error": str(e)}), 500


def _source_comparison(client, now_ms: int) -> list[dict]:
    """Timestamp-aligned comparison of RTDS vs on-chain Chainlink prices.

    Returns last 5 observations where both sources have data within 2s of each other.
    """
    import db

    rtds_ticks = db.get_recent_price_snapshots(
        client, "rtds_chainlink", "BTCUSD", since_ms=now_ms - 60_000, limit=50,
    )
    cl_ticks = db.get_recent_price_snapshots(
        client, "chainlink_onchain", "BTC", since_ms=now_ms - 60_000, limit=50,
    )

    if not rtds_ticks or not cl_ticks:
        return []

    # Build timestamp-indexed lookups
    cl_by_ts = {t.timestamp_ms: t.price for t in cl_ticks}

    comparisons = []
    for rt in reversed(rtds_ticks):
        # Find closest on-chain tick within 2s
        best_ts = min(cl_by_ts.keys(), key=lambda ts: abs(ts - rt.timestamp_ms), default=None)
        if best_ts is None:
            continue
        if abs(best_ts - rt.timestamp_ms) > 2000:
            continue
        delta = rt.price - cl_by_ts[best_ts]
        comparisons.append({
            "rtds_ts": rt.timestamp_ms,
            "chainlink_ts": best_ts,
            "rtds_price": rt.price,
            "chainlink_price": cl_by_ts[best_ts],
            "delta": round(delta, 2),
            "delta_pct": round(delta / cl_by_ts[best_ts] * 100, 4) if cl_by_ts[best_ts] else None,
        })
        if len(comparisons) >= 5:
            break

    return comparisons


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
