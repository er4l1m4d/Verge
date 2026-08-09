-- Phase 1: Add mode column to signals and paper_trades
-- Mode separates safe (filtered) vs risk (forced bet) tracks.
-- All existing rows backfill as 'safe' — they all were.

ALTER TABLE signals ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'safe';
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'safe';

CREATE INDEX IF NOT EXISTS idx_signals_mode ON signals(mode);
CREATE INDEX IF NOT EXISTS idx_paper_trades_mode ON paper_trades(mode);
