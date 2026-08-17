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
            "current_price": sig.current_price,
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

            # 15m strike: prefer official, then Chainlink bars' open, then Coinbase
            if official_strike:
                strike = round(official_strike, 2)
            elif market and market.get("window_open"):
                # Try Chainlink bars first (matches Polymarket's resolution source)
                strike_val = None
                if len(df_recent) > 0:
                    import time as _t
                    matching = df_recent[df_recent["open_time"] >= market["window_open"]]
                    if len(matching) > 0:
                        strike_val = float(matching.iloc[0]["open"])
                # Fallback: Coinbase
                if strike_val is None:
                    from data_fetcher import get_price_at_time
                    strike_val = get_price_at_time(market["window_open"])
                strike = round(strike_val, 2) if strike_val else round(float(df_recent.iloc[0]["open"]), 2)
            else:
                strike = round(float(df_recent.iloc[0]["open"]), 2)
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
    """Ultra-lightweight spot price endpoint for fast polling."""
    try:
        from data_fetcher import get_spot_price
        price = get_spot_price()
        return jsonify({
            "spot": round(price, 2) if price else None,
            "now_ms": int(time.time() * 1000),
        })
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
    """Diagnostics endpoint: source breakdown, live prices, recent signals, accuracy, TWAP vs tick."""
    import time as _time
    import db
    from engine import compute_twap
    from chainlink_fetcher import get_chainlink_price
    from pyth_fetcher import get_pyth_btc_price_value
    from data_fetcher import get_spot_price
    from polymarket_fetcher import get_polymarket_live_market

    now_ms = int(_time.time() * 1000)
    client = db.get_client()

    # 1. Source breakdown (last 24h)
    source_stats = db.get_source_breakdown(client, since_ms=now_ms - 86_400_000)

    # 2. Live prices from each source
    twap_ticks = db.get_recent_price_snapshots(client, "polymarket_rtds", "BTCUSD",
                                                since_ms=now_ms - 90_000)
    from chainlink_ws import get_chainlink_ws_price
    live = {
        "polymarket_rtds_60s_twap_estimate": compute_twap(twap_ticks, now_ms) if len(twap_ticks) >= 2 else None,
        "chainlink_ws": get_chainlink_ws_price(),
        "chainlink_onchain": get_chainlink_price(),
        "pyth": get_pyth_btc_price_value(),
        "coinbase_spot": get_spot_price(),
    }

    # RTDS health info
    from polymarket_rtds import get_rtds_health
    rtds_health = get_rtds_health()
    live["rtds_tick_count_5m"] = len(db.get_recent_price_snapshots(
        client, "polymarket_rtds", "BTCUSD", since_ms=now_ms - 300_000
    ))
    live["rtds_last_tick_age_ms"] = rtds_health["last_tick_age_ms"]

    # Chainlink WSS health info
    from chainlink_ws import get_chainlink_ws_health
    ws_health = get_chainlink_ws_health()
    live["chainlink_ws_age_ms"] = ws_health["last_tick_age_ms"]

    # RPC health info
    from chainlink_fetcher import get_rpc_health
    rpc_health = get_rpc_health()

    # 3. Recent signals with source
    recent_signals = db.get_recent_signals_with_source(client, limit=20)

    # 4. Resolution accuracy by source
    accuracy = db.get_resolution_accuracy_by_source(client)

    # 5. Resolution agreement (Verge vs Polymarket official)
    resolution_agreement = db.get_resolution_agreement(client)

    # 6. TWAP vs single-tick comparison
    last_tick = twap_ticks[-1].price if twap_ticks else None
    twap_val = live["polymarket_rtds_60s_twap_estimate"]
    twap_vs_tick = {
        "twap": twap_val,
        "last_single_tick": last_tick,
        "difference_pct": round(abs(twap_val - last_tick) / last_tick * 100, 4)
            if twap_val and last_tick else None,
    }

    # 7. Polymarket live market comparison (15m + 1h)
    pm_15m = get_polymarket_live_market("btc-up-or-down-15m")
    pm_1h = get_polymarket_live_market("btc-up-or-down-hourly")

    # Compute Verge's best available price for comparison
    verge_price = (
        live.get("polymarket_rtds_60s_twap_estimate")
        or live.get("chainlink_onchain")
        or live.get("pyth")
        or live.get("coinbase_spot")
    )

    def _enrich_pm(pm_data):
        if not pm_data or not verge_price:
            return pm_data
        strike = pm_data.get("price_to_beat")
        return {
            **pm_data,
            "verge_price": verge_price,
            "difference": round(abs(strike - verge_price), 2) if strike else None,
        }

    polymarket_live = {
        "15m": _enrich_pm(pm_15m),
        "1h": _enrich_pm(pm_1h),
    }

    return jsonify({
        "source_breakdown": source_stats,
        "live_prices": live,
        "recent_signals": recent_signals,
        "resolution_accuracy": accuracy,
        "resolution_agreement": resolution_agreement,
        "twap_vs_tick": twap_vs_tick,
        "polymarket_live": polymarket_live,
        "timestamp": now_ms,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
