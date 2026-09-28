-- P3.5.3b evidence exposure columns (schema only, no tables, no backfill).
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260919000000_p35_3b_evidence_exposure.sql, original file lost
-- 2026-09-28); content verified against
-- tests/test_p35_3b_evidence_maturity.py pins.

ALTER TABLE commerce_opportunity_decisions
    ADD COLUMN IF NOT EXISTS exposure_state TEXT,
    ADD COLUMN IF NOT EXISTS exposure_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS exposure_source TEXT;
CREATE INDEX IF NOT EXISTS idx_opportunity_decisions_creator_exposure
    ON commerce_opportunity_decisions (creator_id, exposure_state);
