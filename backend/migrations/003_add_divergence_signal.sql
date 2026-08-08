-- Phase 1.2: Add divergence_signal column to signals table (shadow mode)
-- Odds-vs-momentum divergence signal, logged but not affecting final_decision yet.

ALTER TABLE signals ADD COLUMN IF NOT EXISTS divergence_signal INTEGER DEFAULT 0;
