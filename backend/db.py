"""Supabase Persistence Layer — Phase 5 + 6.

Tables:
  - signals: append-only log of every decision
  - paper_trades: subset that were BET HIGHER/LOWER, with resolution
  - odds_snapshots: raw odds timeline (append-only, every heartbeat tick)
  - price_snapshots: Chainlink price ticks for 15m bar-building (Phase 6.2)
"""
import os
import time
import json
import logging
from dataclasses import dataclass, field

from supabase import create_client, Client

log = logging.getLogger("verge.db")

_supabase_url = os.environ.get("SUPABASE_URL", "")
_supabase_key = os.environ.get("SUPABASE_KEY", "")


def get_client() -> Client:
    """Get Supabase client."""
    return create_client(_supabase_url, _supabase_key)


def get_setting(client: Client, key: str) -> str | None:
    """Read a setting from the settings table. Returns None if not found."""
    try:
        resp = client.table("settings").select("value").eq("key", key).limit(1).execute()
        if resp.data:
            return resp.data[0]["value"]
    except Exception as e:
        log.warning(f"Failed to read setting '{key}': {e}")
    return None


def set_setting(client: Client, key: str, value: str) -> None:
    """Write or update a setting in the settings table."""
    client.table("settings").upsert({"key": key, "value": value}).execute()


def get_frozen_durations(client) -> set[str]:
    """Return the set of currently frozen durations (e.g. {'1h'})."""
    val = get_setting(client, "frozen_durations")
    return set(json.loads(val)) if val else set()


def set_duration_frozen(client, duration: str, frozen: bool) -> None:
    """Add or remove a duration from the frozen set."""
    current = get_frozen_durations(client)
    if frozen:
        current.add(duration)
    else:
        current.discard(duration)
    set_setting(client, "frozen_durations", json.dumps(sorted(current)))


# --- Schema SQL (for reference / manual setup) ---

SCHEMA_SQL = """
-- Phase 5.1: Schema design + Phase 6.1: duration column

CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_window_start BIGINT,
    market_duration TEXT NOT NULL DEFAULT '1h',
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
    note TEXT,
    divergence_signal INTEGER DEFAULT 0,
    fear_greed_value NUMERIC,
    mode TEXT NOT NULL DEFAULT 'safe'
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT REFERENCES signals(id),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_window_start BIGINT,
    market_duration TEXT NOT NULL DEFAULT '1h',
    token_id TEXT,
    decision TEXT NOT NULL,
    odds NUMERIC,
    score NUMERIC,
    edge_pct NUMERIC,
    suggested_price NUMERIC,
    resolved_outcome TEXT,
    simulated_pnl NUMERIC,
    resolved_at TIMESTAMPTZ,
    mode TEXT NOT NULL DEFAULT 'safe'
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id BIGSERIAL PRIMARY KEY,
    token_id TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    price NUMERIC NOT NULL
);

-- Phase 6.2: Chainlink price ticks for 15m bar-building
CREATE TABLE IF NOT EXISTS price_snapshots (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    price NUMERIC NOT NULL
);

-- Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_signals_window ON signals(market_window_start);
CREATE INDEX IF NOT EXISTS idx_signals_token ON signals(token_id);
CREATE INDEX IF NOT EXISTS idx_signals_duration ON signals(market_duration);
CREATE INDEX IF NOT EXISTS idx_paper_trades_window ON paper_trades(market_window_start);
CREATE INDEX IF NOT EXISTS idx_paper_trades_signal ON paper_trades(signal_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_duration ON paper_trades(market_duration);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_token ON odds_snapshots(token_id);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_time ON odds_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_time ON price_snapshots(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_source ON price_snapshots(source);

-- Phase 2: Sim/Live mode toggle
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO settings (key, value) VALUES ('mode', 'paper')
ON CONFLICT (key) DO NOTHING;
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
    market_duration: str = "1h"
    divergence_signal: int = 0
    fear_greed_value: int | None = None
    mode: str = "safe"


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
    market_duration: str = "1h"
    mode: str = "safe"


@dataclass
class PriceSnapshotRow:
    """A price tick for Chainlink bar-building."""
    source: str
    symbol: str
    timestamp_ms: int
    price: float


# --- Write functions ---

def write_signal(client: Client, row: SignalRow) -> int:
    """Insert a signal row. Returns the inserted row ID."""
    data = {
        "market_window_start": row.market_window_start,
        "market_duration": row.market_duration,
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
        "divergence_signal": row.divergence_signal,
        "fear_greed_value": row.fear_greed_value,
        "mode": row.mode,
    }
    result = client.table("signals").insert(data).execute()
    row_id = result.data[0]["id"]
    log.info(f"Wrote signal id={row_id} decision={row.final_decision} duration={row.market_duration}")
    return row_id


def write_paper_trade(client: Client, row: PaperTradeRow) -> int:
    """Insert a paper trade row. Returns the inserted row ID."""
    data = {
        "signal_id": row.signal_id,
        "market_window_start": row.market_window_start,
        "market_duration": row.market_duration,
        "token_id": row.token_id,
        "decision": row.decision,
        "odds": row.odds,
        "score": row.score,
        "edge_pct": row.edge_pct,
        "suggested_price": row.suggested_price,
        "mode": row.mode,
    }
    result = client.table("paper_trades").insert(data).execute()
    row_id = result.data[0]["id"]
    log.info(f"Wrote paper_trade id={row_id} decision={row.decision} duration={row.market_duration}")
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


def write_price_snapshot(client: Client, row: PriceSnapshotRow) -> int:
    """Append a price tick for Chainlink bar-building. Returns inserted row ID."""
    data = {
        "source": row.source,
        "symbol": row.symbol,
        "timestamp_ms": row.timestamp_ms,
        "price": row.price,
    }
    result = client.table("price_snapshots").insert(data).execute()
    return result.data[0]["id"]


# --- Query functions ---

def get_existing_signal(client: Client, window_start: int, duration: str = "1h", mode: str = "safe") -> dict | None:
    """Check if a signal already exists for this market window + duration + mode (idempotency)."""
    result = (
        client.table("signals")
        .select("id")
        .eq("market_window_start", window_start)
        .eq("market_duration", duration)
        .eq("mode", mode)
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


def get_unresolved_trades(client: Client, duration: str | None = None) -> list[dict]:
    """Get paper trades that haven't been resolved yet.

    Args:
        duration: if provided, filter to this duration only
    """
    query = (
        client.table("paper_trades")
        .select("*")
        .is_("resolved_outcome", "null")
    )
    if duration:
        query = query.eq("market_duration", duration)
    result = query.execute()
    return result.data


def get_recent_signals(client: Client, limit: int = 10, duration: str | None = None) -> list[dict]:
    """Get the most recent signals (all decisions, including SKIP) with resolution.

    Uses PostgREST embedded resource query to fetch paper_trades in a single
    round-trip instead of N+1 per-signal queries.

    Args:
        duration: if provided, filter to this duration only
    """
    query = (
        client.table("signals")
        .select(
            "id, final_decision, market_window_start, timestamp, score, market_duration, "
            "strike_price, current_price, odds, edge_pct, rsi, ma_signal, volume_signal, "
            "note, divergence_signal, fear_greed_value, "
            "paper_trades!signal_id(resolved_outcome, simulated_pnl, decision)"
        )
        .eq("mode", "safe")
        .order("id", desc=True)
    )
    if duration:
        query = query.eq("market_duration", duration)
    result = query.limit(limit).execute()
    signals = result.data

    # Extract resolution data from embedded paper_trades
    for sig in signals:
        trades = sig.pop("paper_trades", [])
        if trades:
            sig["resolved_outcome"] = trades[0].get("resolved_outcome")
            sig["simulated_pnl"] = trades[0].get("simulated_pnl")
        else:
            sig["resolved_outcome"] = None
            sig["simulated_pnl"] = None

    return signals


def get_performance_summary(client: Client, duration: str | None = None) -> dict:
    """Rolling performance over last 200 signals.

    Uses PostgREST embedded resource query to avoid N+1 per-signal round-trips.
    Returns total signals in window, profitable count, cumulative ROI%,
    and recent resolved trades (max 10, most recent first).
    """
    query = (
        client.table("signals")
        .select(
            "id, final_decision, market_window_start, market_duration, odds, "
            "paper_trades!signal_id(resolved_outcome, simulated_pnl, decision)"
        )
        .eq("mode", "safe")
        .order("id", desc=True)
    )
    if duration:
        query = query.eq("market_duration", duration)
    signals = query.limit(200).execute().data

    total = len(signals)
    if total == 0:
        return {"total_signals": 0, "window": 200, "profitable": 0, "resolved": 0, "roi_pct": 0.0, "recent_resolved": []}

    profitable = 0
    total_pnl = 0.0
    resolved_count = 0
    recent_resolved = []

    for sig in signals:
        trades = sig.get("paper_trades", [])
        if not trades:
            continue
        trade = trades[0]
        resolved = trade.get("resolved_outcome")
        if not resolved:
            continue
        resolved_count += 1
        pnl = trade.get("simulated_pnl", 0) or 0
        total_pnl += pnl
        if pnl > 0:
            profitable += 1
        if len(recent_resolved) < 10:
            recent_resolved.append({
                "market_window_start": sig["market_window_start"],
                "market_duration": sig.get("market_duration", "1h"),
                "decision": trade.get("decision", sig.get("final_decision")),
                "resolved_outcome": resolved,
                "simulated_pnl": round(pnl, 4),
            })

    roi_pct = (total_pnl / resolved_count * 100) if resolved_count > 0 else 0.0

    return {
        "total_signals": total,
        "window": 200,
        "profitable": profitable,
        "resolved": resolved_count,
        "roi_pct": round(roi_pct, 1),
        "recent_resolved": recent_resolved,
    }


def get_paginated_signals(
    client: Client, offset: int = 0, limit: int = 25, duration: str | None = None
) -> dict:
    """Paginated signal log with resolution data.

    Uses PostgREST embedded resource query to avoid N+1 per-signal round-trips.
    Returns {signals: [...], total: N}.
    """
    # Count total
    count_query = client.table("signals").select("id", count="exact").eq("mode", "safe")
    if duration:
        count_query = count_query.eq("market_duration", duration)
    count_result = count_query.execute()
    total = count_result.count if hasattr(count_result, "count") else 0

    # Fetch page with embedded paper_trades
    query = (
        client.table("signals")
        .select(
            "id, final_decision, market_window_start, timestamp, score, market_duration, "
            "strike_price, current_price, odds, edge_pct, rsi, ma_signal, volume_signal, "
            "note, divergence_signal, fear_greed_value, "
            "paper_trades!signal_id(resolved_outcome, simulated_pnl, decision)"
        )
        .eq("mode", "safe")
        .order("id", desc=True)
    )
    if duration:
        query = query.eq("market_duration", duration)
    result = query.range(offset, offset + limit - 1).execute()
    signals = result.data

    # Extract resolution data from embedded paper_trades
    for sig in signals:
        trades = sig.pop("paper_trades", [])
        if trades:
            sig["resolved_outcome"] = trades[0].get("resolved_outcome")
            sig["simulated_pnl"] = trades[0].get("simulated_pnl")
        else:
            sig["resolved_outcome"] = None
            sig["simulated_pnl"] = None

    return {"signals": signals, "total": total}


def get_batch_summaries(client: Client, duration: str | None = None, batch_size: int = 200) -> dict:
    """Per-batch performance summaries for the signal log.

    Groups signals into batches of `batch_size` and returns summary stats
    for each batch: count, wins, losses, skips, total P&L, win rate, ROI%.

    Returns {batches: [...], total_batches: N}.
    """
    # Fetch all matching signals with their paper_trades
    query = (
        client.table("signals")
        .select(
            "id, final_decision, market_duration, "
            "paper_trades!signal_id(resolved_outcome, simulated_pnl, decision)"
        )
        .eq("mode", "safe")
        .order("id", desc=True)
    )
    if duration:
        query = query.eq("market_duration", duration)
    signals = query.execute().data

    total = len(signals)
    if total == 0:
        return {"batches": [], "total_batches": 0}

    batches = []
    for i in range(0, total, batch_size):
        chunk = signals[i:i + batch_size]
        batch_num = (i // batch_size) + 1

        wins = 0
        losses = 0
        skips = 0
        total_pnl = 0.0

        for sig in chunk:
            decision = sig.get("final_decision") or "SKIP"
            trades = sig.get("paper_trades", [])
            if not trades or decision == "SKIP":
                skips += 1
                continue
            trade = trades[0]
            resolved = trade.get("resolved_outcome")
            pnl = trade.get("simulated_pnl", 0) or 0
            if not resolved:
                skips += 1
                continue
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1

        resolved_count = wins + losses
        win_rate = round(wins / resolved_count * 100, 1) if resolved_count > 0 else 0
        roi_pct = round(total_pnl / resolved_count * 100, 1) if resolved_count > 0 else 0

        batches.append({
            "batch": batch_num,
            "offset": i,
            "count": len(chunk),
            "wins": wins,
            "losses": losses,
            "skips": skips,
            "total_pnl": round(total_pnl, 2),
            "win_rate": win_rate,
            "roi_pct": roi_pct,
        })

    return {"batches": batches, "total_batches": len(batches)}


def get_stats(client: Client, duration: str | None = None) -> dict:
    """Aggregate stats over paper_trades, including recent trades for history band.

    Args:
        duration: if provided, filter to this duration only.
                  If None, returns combined stats across all durations.
    """
    query = client.table("paper_trades").select(
        "decision, resolved_outcome, simulated_pnl, market_window_start, market_duration"
    ).eq("mode", "safe").order("id", desc=True)
    if duration:
        query = query.eq("market_duration", duration)
    trades = query.execute().data

    total = len(trades)
    resolved = [t for t in trades if t.get("resolved_outcome")]
    wins = [t for t in resolved if t.get("simulated_pnl", 0) > 0]
    losses = [t for t in resolved if t.get("simulated_pnl", 0) < 0]
    cumulative_pnl = sum(t.get("simulated_pnl", 0) for t in resolved)
    win_rate = len(wins) / len(resolved) * 100 if resolved else 0

    # Recent trades for history band (last 10, most recent first)
    recent = resolved[:10] if resolved else []

    # Recent signals for signal log (all decisions, last 10)
    recent_signals = get_recent_signals(client, limit=10, duration=duration)

    # Graduation gate: count all trades (all are safe mode now)
    safe_resolved = [t for t in trades if t.get("resolved_outcome")]
    safe_total = len(trades)
    safe_cumulative_pnl = sum(t.get("simulated_pnl", 0) for t in safe_resolved)
    unlock_real_orders = safe_total >= 200 and safe_cumulative_pnl > 0

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


def get_rolling_stats(client: Client, duration: str | None = None, window: int = 30) -> dict:
    """Rolling-window performance over last N resolved trades.

    Catches strategy degradation that cumulative stats can hide.

    Returns dict with:
        rolling_win_rate: win rate over last N resolved trades
        rolling_pnl: cumulative P&L over window
        rolling_roi_pct: ROI% over window
        rolling_count: number of resolved trades in window
    """
    query = (
        client.table("paper_trades")
        .select("simulated_pnl, resolved_outcome")
        .eq("mode", "safe")
        .not_.is_("resolved_outcome", "null")
        .order("id", desc=True)
    )
    if duration:
        query = query.eq("market_duration", duration)
    trades = query.limit(window).execute().data

    count = len(trades)
    if count == 0:
        return {"rolling_win_rate": 0, "rolling_pnl": 0, "rolling_roi_pct": 0, "rolling_count": 0}

    wins = sum(1 for t in trades if (t.get("simulated_pnl") or 0) > 0)
    total_pnl = sum(t.get("simulated_pnl") or 0 for t in trades)
    win_rate = wins / count * 100
    roi_pct = total_pnl / count * 100 if count > 0 else 0

    return {
        "rolling_win_rate": round(win_rate, 1),
        "rolling_pnl": round(total_pnl, 2),
        "rolling_roi_pct": round(roi_pct, 1),
        "rolling_count": count,
    }




def get_price_snapshots(
    client: Client,
    source: str = "chainlink",
    symbol: str = "BTC",
    since_ms: int | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Get accumulated price ticks for Chainlink bar-building.

    Args:
        source: price source filter (e.g. "chainlink")
        symbol: symbol filter (e.g. "BTC")
        since_ms: if provided, only return ticks after this timestamp
        limit: max rows to return
    """
    query = (
        client.table("price_snapshots")
        .select("timestamp_ms, price")
        .eq("source", source)
        .eq("symbol", symbol)
        .order("timestamp_ms", desc=False)
    )
    if since_ms:
        query = query.gte("timestamp_ms", since_ms)
    result = query.limit(limit).execute()
    return result.data
