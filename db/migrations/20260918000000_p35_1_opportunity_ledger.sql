-- P3.5.1 opportunity attribution ledger (schema only, no backfill).
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260918000000_p35_1_opportunity_ledger.sql, original file lost
-- 2026-09-28); content verified against
-- tests/test_p35_1_attribution_ledger.py pins and live staging DDL.

CREATE TABLE IF NOT EXISTS commerce_opportunity_decisions (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    user_id BIGINT,
    generation_id TEXT,
    definition_id BIGINT,
    sealed_offer_id BIGINT,
    outcome_state TEXT,
    reengagement_of BIGINT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_opportunity_decisions_creator_generation
    ON commerce_opportunity_decisions (creator_id, generation_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_decisions_reengage_dedup
    ON commerce_opportunity_decisions (creator_id, reengagement_of)
    WHERE reengagement_of IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_opportunity_decisions_creator_definition
    ON commerce_opportunity_decisions (creator_id, definition_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_decisions_creator_outcome
    ON commerce_opportunity_decisions (creator_id, outcome_state);
CREATE INDEX IF NOT EXISTS idx_opportunity_decisions_sealed_offer
    ON commerce_opportunity_decisions (sealed_offer_id)
    WHERE sealed_offer_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_opportunity_decisions_reengagement_of
    ON commerce_opportunity_decisions (reengagement_of)
    WHERE reengagement_of IS NOT NULL;
