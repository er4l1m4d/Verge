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

from engine import generate_signal, persist_signal, resolve_previous_hour
from telegram import alert_on_signal

app = Flask(__name__)

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


@app.route("/api/signal")
@require_secret
def signal():
    """4.4 — Signal endpoint.

    Returns the full live signal as JSON.
    """
    try:
        sig = generate_signal()
        return jsonify({
            "decision": sig.decision,
            "final_decision": sig.final_decision,
            "confidence": sig.confidence,
            "score": sig.score,
            "model_probability": sig.model_probability,
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
    """Fetch 5m candles for the current hour for the mini-chart.

    Returns candle data + strike price + spot price for real-time chart rendering.
    """
    try:
        import time as _time
        from data_fetcher import get_price_with_fallback, get_spot_price

        now_ms = int(_time.time() * 1000)
        start_ms = now_ms - (16 * 5 * 60 * 1000)

        df = get_price_with_fallback(
            symbol="BTCUSDT",
            interval="5m",
            start_time=start_ms,
            end_time=now_ms,
        )

        if df is None or len(df) == 0:
            return jsonify({"candles": [], "strike": None, "spot": None, "now_ms": now_ms})

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

        strike = round(float(df_hour.iloc[0]["open"]), 2)
        spot = get_spot_price()

        return jsonify({
            "candles": candles_list,
            "strike": strike,
            "spot": round(spot, 2) if spot else None,
            "now_ms": now_ms,
        })
    except Exception as e:
        log.warning(f"Candles fetch failed: {e}")
        return jsonify({"candles": [], "strike": None, "spot": None, "now_ms": int(time.time() * 1000)})


@app.route("/api/heartbeat")
@require_secret
def heartbeat():
    """6.1 — Heartbeat endpoint.

    Generates signal, persists to Supabase, resolves previous hour.
    Idempotent: won't duplicate signals for the same market window.
    """
    try:
        sig = generate_signal()

        # Persist to database
        try:
            persist_signal(sig)
        except Exception as e:
            log.warning(f"Persist failed (non-fatal): {e}")

        # Telegram alert (only on BET decisions)
        try:
            alert_on_signal(sig)
        except Exception as e:
            log.warning(f"Telegram alert failed (non-fatal): {e}")

        # Try to resolve previous hour's trades
        try:
            resolve_previous_hour()
        except Exception as e:
            log.warning(f"Resolution check failed (non-fatal): {e}")

        return jsonify({
            "status": "ok",
            "timestamp": int(time.time()),
            "decision": sig.final_decision,
            "market": sig.market_slug,
            "minutes_remaining": sig.minutes_remaining,
        })
    except Exception as e:
        log.exception("Heartbeat failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/stats")
@require_secret
def stats():
    """5.4 — Stats endpoint.

    Aggregate stats over paper_trades.
    """
    try:
        import db
        client = db.get_client()
        result = db.get_stats(client)
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
            "recent_trades": [],
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
