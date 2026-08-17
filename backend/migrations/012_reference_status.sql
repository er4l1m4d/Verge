-- Migration 012: Add reference_status to signals
-- "estimated" = local TWAP reconstruction (not official Polymarket source)
-- "fallback"  = fell back to Chainlink/Pyth/Coinbase/Binance
-- "official"  = Polymarket's own price feed (reserved for future use)

ALTER TABLE signals ADD COLUMN IF NOT EXISTS reference_status TEXT DEFAULT 'estimated';
