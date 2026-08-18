-- Migration 014: Add strike_source, reference_age_ms, quality_status to signals
-- Enables price-reference audit: every signal records which source produced the strike

ALTER TABLE signals ADD COLUMN IF NOT EXISTS strike_source TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reference_age_ms INTEGER;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS quality_status TEXT DEFAULT 'estimated';

-- quality_status trust tiers:
--   'good'      → gamma_price_to_beat | rtds_twap_60s
--   'degraded'  → chainlink_onchain_twap_60s
--   'fallback'  → binance_candle | coinbase_spot | candle_close
--   'estimated' → default (pre-migration rows)
