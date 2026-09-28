-- P2.1 first-sale reconstruction columns on users (fan lifecycle).
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260914000000_p21_first_sale.sql, original file lost 2026-09-28);
-- content verified against tests/test_p21_first_sale.py pins.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS first_purchase_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS first_offer_id BIGINT,
    ADD COLUMN IF NOT EXISTS first_transaction_id TEXT;

CREATE TABLE IF NOT EXISTS ambiguous_purchase_recoveries (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transaction_id TEXT,
    reason TEXT NOT NULL DEFAULT '',
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ambiguous_purchase_recoveries_user
    ON ambiguous_purchase_recoveries (creator_id, user_id, created_at DESC);
