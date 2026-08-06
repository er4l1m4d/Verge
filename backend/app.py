"""Verge Backend API — Phase 4–5.

Flask app exposing:
  GET /api/signal   — live signal (4.4)
  GET /api/health   — health check (4.5)
  GET /api/heartbeat — heartbeat with persistence (6.1)
  GET /api/stats    — aggregate stats (5.4)
"""
import os
import sys
import logging
import time
from flask import Flask, jsonify
from flask_cors import CORS

sys.path.insert(0, os.path.dirname(__file__))

from engine import generate_signal, persist_signal, resolve_previous_hour
from telegram import alert_on_signal

app = Flask(__name__)

# CORS: restrict to Vercel frontend in production, allow all in dev
VERCEL_URL = os.environ.get("VERCEL_URL", "")
cors_origins = [f"https://{VERCEL_URL}"] if VERCEL_URL else ["*"]
CORS(app, origins=cors_origins)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("verge.api")


@app.route("/api/health")
def health():
    """4.5 — Health endpoint."""
    return jsonify({
        "status": "ok",
        "timestamp": int(time.time()),
        "service": "verge-backend",
    })


@app.route("/api/signal")
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
                "minutes_remaining": sig.minutes_remaining,
            },
            "odds": sig.odds,
            "edge_pct": sig.edge_pct,
            "fee_eroded": sig.fee_eroded,
            "suggested_price": sig.suggested_price,
            "note": sig.note,
        })
    except Exception as e:
        log.exception("Signal generation failed")
        return jsonify({
            "error": str(e),
            "decision": "SKIP",
            "final_decision": "SKIP",
        }), 500


@app.route("/api/heartbeat")
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
        log.exception("Stats query failed")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
