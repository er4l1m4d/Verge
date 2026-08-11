-- Universal window outcomes: records the true UP/DOWN result for every window,
-- not just ones where a bet was placed. Closes the sampling bias gap.
CREATE TABLE IF NOT EXISTS window_outcomes (
    market_duration TEXT NOT NULL,
    market_window_start BIGINT NOT NULL,
    actual_outcome TEXT NOT NULL,  -- 'UP' or 'DOWN'
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (market_duration, market_window_start)
);
CREATE INDEX IF NOT EXISTS idx_window_outcomes_duration
    ON window_outcomes(market_duration);

-- RLS: allow anon key (backend service) full access (matches 002 pattern)
ALTER TABLE window_outcomes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON window_outcomes
    FOR ALL USING (auth.role() = 'anon');
