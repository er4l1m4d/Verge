"""Supabase Persistence Layer — Phase 5.

Tables:
  - signals: append-only log of every decision
  - paper_trades: subset that were BET HIGHER/LOWER, with resolution
  - odds_snapshots: raw odds timeline (append-only, every heartbeat tick)
"""
import os
import time
import logging
from dataclasses import dataclass

from supabase import create_client, Client

log = logging.getLogger("verge.db")

_supabase_url = os.environ.get("SUPABASE_URL", "")
_supabase_key = os.environ.get("SUPABASE_KEY", "")


def get_client() -> Client:
    """Get Supabase client."""
    return create_client(_supabase_url, _supabase_key)


# --- Schema SQL (for reference / manual setup) ---

SCHEMA_SQL = """
-- Phase 5.1: Schema design

CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_window_start BIGINT,
    token_id TEXT,
    decision TEXT NOT NULL,
    final_decision TEXT NOT NULL,
    confidence TEXT,
    score NUMERIC,
    model_probability NUMERIC,
    rsi NUMERIC,
    ma_signal INTEGER,
    volume_signal INTEGER,
    odds NUMERIC,
    edge_pct NUMERIC,
    fee_eroded BOOLEAN DEFAULT FALSE,
    suggested_price NUMERIC,
    minutes_remaining INTEGER,
    strike_price NUMERIC,
    current_price NUMERIC,
    note TEXT
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT REFERENCES signals(id),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_window_start BIGINT,
    token_id TEXT,
    decision TEXT NOT NULL,
    odds NUMERIC,
    score NUMERIC,
    edge_pct NUMERIC,
    suggested_price NUMERIC,
    resolved_outcome TEXT,
    simulated_pnl NUMERIC,
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id BIGSERIAL PRIMARY KEY,
    token_id TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    price NUMERIC NOT NULL
);

-- Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_signals_window ON signals(market_window_start);
CREATE INDEX IF NOT EXISTS idx_signals_token ON signals(token_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_window ON paper_trades(market_window_start);
CREATE INDEX IF NOT EXISTS idx_paper_trades_signal ON paper_trades(signal_id);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_token ON odds_snapshots(token_id);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_time ON odds_snapshots(timestamp);
"""


# --- Data classes ---

@dataclass
class SignalRow:
    """A signal row for insertion."""
    market_window_start: int
    token_id: str
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
    suggested_price: float | None
    minutes_remaining: int
    strike_price: float | None = None
    current_price: float | None = None
    note: str | None = None


@dataclass
class PaperTradeRow:
    """A paper trade row for insertion."""
    signal_id: int
    market_window_start: int
    token_id: str
    decision: str
    odds: float
    score: float
    edge_pct: float
    suggested_price: float | None


# --- Write functions ---

def write_signal(client: Client, row: SignalRow) -> int:
    """Insert a signal row. Returns the inserted row ID."""
    data = {
        "market_window_start": row.market_window_start,
        "token_id": row.token_id,
        "decision": row.decision,
        "final_decision": row.final_decision,
        "confidence": row.confidence,
        "score": row.score,
        "model_probability": row.model_probability,
        "rsi": row.rsi,
        "ma_signal": row.ma_signal,
        "volume_signal": row.volume_signal,
        "odds": row.odds,
        "edge_pct": row.edge_pct,
        "fee_eroded": row.fee_eroded,
        "suggested_price": row.suggested_price,
        "minutes_remaining": row.minutes_remaining,
        "strike_price": row.strike_price,
        "current_price": row.current_price,
        "note": row.note,
    }
    result = client.table("signals").insert(data).execute()
    row_id = result.data[0]["id"]
    log.info(f"Wrote signal id={row_id} decision={row.final_decision}")
    return row_id


def write_paper_trade(client: Client, row: PaperTradeRow) -> int:
    """Insert a paper trade row. Returns the inserted row ID."""
    data = {
        "signal_id": row.signal_id,
        "market_window_start": row.market_window_start,
        "token_id": row.token_id,
        "decision": row.decision,
        "odds": row.odds,
        "score": row.score,
        "edge_pct": row.edge_pct,
        "suggested_price": row.suggested_price,
    }
    result = client.table("paper_trades").insert(data).execute()
    row_id = result.data[0]["id"]
    log.info(f"Wrote paper_trade id={row_id} decision={row.decision}")
    return row_id


def write_odds_snapshot(client: Client, token_id: str, price: float) -> int:
    """Append an odds snapshot row. Returns the inserted row ID."""
    now_ms = int(time.time() * 1000)
    data = {
        "token_id": token_id,
        "timestamp": now_ms,
        "price": price,
    }
    result = client.table("odds_snapshots").insert(data).execute()
    row_id = result.data[0]["id"]
    return row_id


# --- Query functions ---

def get_existing_signal(client: Client, window_start: int) -> dict | None:
    """Check if a signal already exists for this market window (idempotency)."""
    result = (
        client.table("signals")
        .select("id")
        .eq("market_window_start", window_start)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def update_paper_trade_resolution(
    client: Client,
    signal_id: int,
    resolved_outcome: str,
    simulated_pnl: float,
) -> None:
    """Update a paper trade with resolution data."""
    import datetime
    client.table("paper_trades").update({
        "resolved_outcome": resolved_outcome,
        "simulated_pnl": simulated_pnl,
        "resolved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }).eq("signal_id", signal_id).execute()
    log.info(f"Resolved paper_trade signal_id={signal_id} outcome={resolved_outcome} pnl={simulated_pnl}")


def get_unresolved_trades(client: Client) -> list[dict]:
    """Get paper trades that haven't been resolved yet."""
    result = (
        client.table("paper_trades")
        .select("*")
        .is_("resolved_outcome", "null")
        .execute()
    )
    return result.data


def get_recent_signals(client: Client, limit: int = 10) -> list[dict]:
    """Get the most recent signals (all decisions, including SKIP)."""
    result = (
        client.table("signals")
        .select("final_decision, market_window_start, timestamp, score")
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


def get_stats(client: Client) -> dict:
    """Aggregate stats over paper_trades, including recent trades for history band."""
    result = client.table("paper_trades").select("decision, resolved_outcome, simulated_pnl, market_window_start").order("id", desc=True).execute()
    trades = result.data

    total = len(trades)
    resolved = [t for t in trades if t.get("resolved_outcome")]
    wins = [t for t in resolved if t.get("simulated_pnl", 0) > 0]
    losses = [t for t in resolved if t.get("simulated_pnl", 0) < 0]
    cumulative_pnl = sum(t.get("simulated_pnl", 0) for t in resolved)
    win_rate = len(wins) / len(resolved) * 100 if resolved else 0

    # Recent trades for history band (last 10, most recent first)
    recent = resolved[:10] if resolved else []

    # Recent signals for signal log (all decisions, last 10)
    recent_signals = get_recent_signals(client, limit=10)

    # 9.2 — Graduation gate: ≥200 trades AND positive cumulative ROI
    unlock_real_orders = total >= 200 and cumulative_pnl > 0

    return {
        "total_trades": total,
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "cumulative_pnl": round(cumulative_pnl, 2),
        "unlock_real_orders": unlock_real_orders,
        "recent_trades": recent,
        "recent_signals": recent_signals,
    }
