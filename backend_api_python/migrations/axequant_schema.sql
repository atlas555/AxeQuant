-- AxeQuant Schema Additions
-- Applied after init.sql in the PostgreSQL container init pipeline.
-- All tables prefixed `bts_` to isolate from upstream QD schema.

-- =============================================================================
-- Phase 2: Defense Reports (WFA / CPCV / DSR)
-- =============================================================================

CREATE TABLE IF NOT EXISTS bts_defense_reports (
    job_id VARCHAR(32) PRIMARY KEY,
    user_id INTEGER,
    strategy_id VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    request JSONB NOT NULL,
    result JSONB,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_bts_defense_user ON bts_defense_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_bts_defense_strategy ON bts_defense_reports(strategy_id);
CREATE INDEX IF NOT EXISTS idx_bts_defense_status ON bts_defense_reports(status);

-- =============================================================================
-- Phase 3: Autoresearch Reports + Candidates
-- =============================================================================

CREATE TABLE IF NOT EXISTS bts_autoresearch_reports (
    job_id VARCHAR(32) PRIMARY KEY,
    user_id INTEGER,
    strategy_id VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    request JSONB NOT NULL,
    result JSONB,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_bts_autoresearch_user ON bts_autoresearch_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_bts_autoresearch_strategy ON bts_autoresearch_reports(strategy_id);

CREATE TABLE IF NOT EXISTS bts_autoresearch_candidates (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(32) REFERENCES bts_autoresearch_reports(job_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    params JSONB NOT NULL,
    oos_sharpe DOUBLE PRECISION,
    n_trades INTEGER,
    defense_job_id VARCHAR(32),
    verdict VARCHAR(16)
);

CREATE INDEX IF NOT EXISTS idx_bts_candidates_job ON bts_autoresearch_candidates(job_id);
CREATE INDEX IF NOT EXISTS idx_bts_candidates_sharpe ON bts_autoresearch_candidates(oos_sharpe);

-- =============================================================================
-- Phase 4: Paper Trading Runs + Snapshots
-- =============================================================================

CREATE TABLE IF NOT EXISTS bts_paper_runs (
    id VARCHAR(32) PRIMARY KEY,
    user_id INTEGER,
    strategy_id VARCHAR(64),
    candidate_id INTEGER,
    params JSONB NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    testnet BOOLEAN NOT NULL DEFAULT TRUE,
    initial_capital DOUBLE PRECISION NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'starting',
    config JSONB,
    started_at TIMESTAMP WITH TIME ZONE,
    stopped_at TIMESTAMP WITH TIME ZONE,
    drift_violations INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bts_paper_user ON bts_paper_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_bts_paper_status ON bts_paper_runs(status);

CREATE TABLE IF NOT EXISTS bts_paper_snapshots (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(32) REFERENCES bts_paper_runs(id) ON DELETE CASCADE,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    equity DOUBLE PRECISION NOT NULL,
    position_size DOUBLE PRECISION,
    position_side VARCHAR(8)
);

CREATE INDEX IF NOT EXISTS idx_bts_snapshots_run_ts ON bts_paper_snapshots(run_id, ts);

-- =============================================================================
-- Phase 5: Live Trading Runs + Immutable Audit Log
-- =============================================================================

CREATE TABLE IF NOT EXISTS bts_live_runs (
    id VARCHAR(32) PRIMARY KEY,
    user_id INTEGER,
    paper_run_id VARCHAR(32),
    strategy_id VARCHAR(64),
    params JSONB NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    capital DOUBLE PRECISION NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'starting',
    config JSONB,
    qualification JSONB,
    started_at TIMESTAMP WITH TIME ZONE,
    killed_at TIMESTAMP WITH TIME ZONE,
    kill_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_bts_live_user ON bts_live_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_bts_live_status ON bts_live_runs(status);

CREATE TABLE IF NOT EXISTS bts_audit_log (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(32) NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    hash VARCHAR(64) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bts_audit_run_ts ON bts_audit_log(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_bts_audit_event_type ON bts_audit_log(event_type);

-- Enforce immutability at the DB level — reject UPDATE/DELETE on audit log
CREATE OR REPLACE FUNCTION bts_audit_log_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'bts_audit_log is append-only (op=%)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bts_audit_log_no_update ON bts_audit_log;
CREATE TRIGGER bts_audit_log_no_update
    BEFORE UPDATE OR DELETE ON bts_audit_log
    FOR EACH ROW EXECUTE FUNCTION bts_audit_log_reject_mutation();

-- =============================================================================
-- End AxeQuant schema
-- =============================================================================
