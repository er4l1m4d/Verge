-- Migration 010: Resolution validation — condition_id + official outcome tracking

-- signals: store Polymarket condition_id for resolution queries
ALTER TABLE signals ADD COLUMN IF NOT EXISTS condition_id TEXT;

-- window_outcomes: store Polymarket's official outcome for validation
ALTER TABLE window_outcomes ADD COLUMN IF NOT EXISTS official_outcome TEXT;
ALTER TABLE window_outcomes ADD COLUMN IF NOT EXISTS resolution_agreement BOOLEAN;
