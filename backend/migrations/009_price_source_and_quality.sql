-- Migration 009: Add source-labeling and quality tracking columns
-- price_snapshots: quality visibility for TWAP validation
-- signals: track which source supplied each price

ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS quality_note TEXT;
ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS age_ms INTEGER;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS price_source TEXT;
