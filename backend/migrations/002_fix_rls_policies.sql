-- RLS policies: allow anon key (backend service) full CRUD on all tables.
-- API-level auth (VERGE_SECRET) provides the outer security layer.

-- 1. Ensure RLS is enabled (default in Supabase, but be explicit)
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE odds_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_snapshots ENABLE ROW LEVEL SECURITY;

-- 2. Drop any conflicting existing policies
DROP POLICY IF EXISTS "Allow all" ON signals;
DROP POLICY IF EXISTS "Allow all" ON paper_trades;
DROP POLICY IF EXISTS "Allow all" ON odds_snapshots;
DROP POLICY IF EXISTS "Allow all" ON price_snapshots;

-- 3. Create permissive policies for the anon role (used by SUPABASE_KEY)
CREATE POLICY "anon_full_access" ON signals
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "anon_full_access" ON paper_trades
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "anon_full_access" ON odds_snapshots
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "anon_full_access" ON price_snapshots
  FOR ALL USING (true) WITH CHECK (true);
