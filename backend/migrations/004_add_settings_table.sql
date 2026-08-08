-- Phase 2: Sim/Live mode toggle table
-- Groundwork for future real-order execution. Default mode is "paper".

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO settings (key, value) VALUES ('mode', 'paper')
ON CONFLICT (key) DO NOTHING;
