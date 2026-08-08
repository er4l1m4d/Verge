-- Migration 003+004 combined: divergence_signal column + settings table
-- Safe to run even if previous migrations have already been applied.

-- 1. Add divergence_signal column to signals (Phase 1.2 — shadow mode)
ALTER TABLE signals ADD COLUMN IF NOT EXISTS divergence_signal INTEGER DEFAULT 0;

-- 2. Create settings table (Phase 2 — Sim/Live mode toggle)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO settings (key, value) VALUES ('mode', 'paper')
ON CONFLICT (key) DO NOTHING;

-- 3. Enable RLS on settings (matches pattern from 002_fix_rls_policies.sql)
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

-- 4. Drop conflicting policy if it exists, then create permissive anon policy
DROP POLICY IF EXISTS "anon_full_access" ON settings;
CREATE POLICY "anon_full_access" ON settings
  FOR ALL USING (true) WITH CHECK (true);
