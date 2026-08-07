-- Migration 001: Add duration support for 15m markets (Phase 6)
-- Run this in Supabase SQL Editor before deploying Phase 6 backend.

-- 6.1: Add market_duration column to signals
ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_duration TEXT NOT NULL DEFAULT '1h';

-- 6.1: Add market_duration column to paper_trades
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS market_duration TEXT NOT NULL DEFAULT '1h';

-- 6.2: Chainlink price ticks for 15m bar-building
CREATE TABLE IF NOT EXISTS price_snapshots (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    price NUMERIC NOT NULL
);

-- Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_signals_duration ON signals(market_duration);
CREATE INDEX IF NOT EXISTS idx_paper_trades_duration ON paper_trades(market_duration);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_time ON price_snapshots(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_source ON price_snapshots(source);
