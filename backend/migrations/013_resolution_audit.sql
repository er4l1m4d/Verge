-- Migration 013: Resolution audit table
-- Captures strike method per resolved window for empirical validation
-- of price-to-beat semantics against Polymarket's ground truth.

CREATE TABLE IF NOT EXISTS resolution_audit (
    id BIGSERIAL PRIMARY KEY,
    window_start BIGINT NOT NULL,
    window_close BIGINT NOT NULL,
    duration TEXT NOT NULL,
    local_outcome TEXT NOT NULL,
    official_outcome TEXT,
    agreement BOOLEAN,
    strike_method TEXT,
    strike_price NUMERIC,
    twap_price NUMERIC,
    open_price NUMERIC,
    tick_count INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_duration
    ON resolution_audit(duration);
CREATE INDEX IF NOT EXISTS idx_resolution_audit_window
    ON resolution_audit(window_start);
