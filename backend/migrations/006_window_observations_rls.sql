-- RLS for window_observations: allow anon key full CRUD (matches 002_fix_rls_policies.sql pattern).

ALTER TABLE window_observations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_full_access" ON window_observations;

CREATE POLICY "anon_full_access" ON window_observations
  FOR ALL USING (true) WITH CHECK (true);
