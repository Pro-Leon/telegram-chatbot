-- PPV intelligence: eligibility decisions + daily analytics rollups.
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260819100000_ppv_intelligence.sql, original file lost 2026-09-28);
-- content verified against tests/test_ppv_intelligence.py pins and live
-- staging DDL.

CREATE TABLE IF NOT EXISTS ppv_eligibility_decisions (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id BIGINT,
    decision BOOLEAN NOT NULL,
    denial_reason TEXT,
    inputs JSONB NOT NULL DEFAULT '{}',
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ppv_eligibility_user_evaluated
    ON ppv_eligibility_decisions (user_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ppv_eligibility_creator_product_decision
    ON ppv_eligibility_decisions (creator_id, product_id, decision);
CREATE INDEX IF NOT EXISTS idx_ppv_eligibility_creator_evaluated
    ON ppv_eligibility_decisions (creator_id, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS ppv_analytics_daily (
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL,
    day DATE NOT NULL,
    offers INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    purchases BIGINT NOT NULL DEFAULT 0,
    revenue_minor BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (creator_id, product_id, day)
);
CREATE INDEX IF NOT EXISTS idx_ppv_analytics_day
    ON ppv_analytics_daily (day DESC);
