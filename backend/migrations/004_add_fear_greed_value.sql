-- Add fear_greed_value column to signals table
-- Tracks the daily Fear & Greed index at time of signal generation

ALTER TABLE signals ADD COLUMN IF NOT EXISTS fear_greed_value SMALLINT;

COMMENT ON COLUMN signals.fear_greed_value IS 'Daily Fear & Greed index (0-100) at time of signal generation';
