-- Migration 011: Add market_id (numeric Gamma API id) to signals and window_outcomes
-- The condition_id (hex) doesn't work with Gamma API condition_ids filter for 15m markets.
-- The numeric market id works reliably via /markets/{id} endpoint.

ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_id TEXT;
ALTER TABLE window_outcomes ADD COLUMN IF NOT EXISTS market_id TEXT;
