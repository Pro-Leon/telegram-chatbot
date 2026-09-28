-- Free-photo ledger: daily per-fan free delivery allowance (seq 1..4).
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260912000000_free_photo_ledger.sql, original file lost 2026-09-28);
-- content verified against tests/test_phase97_free_photo_ledger.py pins.
-- Touches only its own tables: no ALTER of existing tables.

CREATE TABLE IF NOT EXISTS free_photo_deliveries (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day_bucket DATE NOT NULL,
    seq INTEGER NOT NULL CHECK (seq BETWEEN 1 AND 4),
    vault_item_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending','sent','failed')) DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    CONSTRAINT uq_free_photo_deliveries_day_seq
        UNIQUE (creator_id, user_id, day_bucket, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_free_photo_deliveries_media_day
    ON free_photo_deliveries (creator_id, user_id, day_bucket, vault_item_id)
    WHERE vault_item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_free_photo_deliveries_auth
    ON free_photo_deliveries (creator_id, user_id, day_bucket, status);
CREATE INDEX IF NOT EXISTS idx_free_photo_deliveries_creator_day
    ON free_photo_deliveries (creator_id, day_bucket, seq);
CREATE INDEX IF NOT EXISTS idx_free_photo_deliveries_user_day
    ON free_photo_deliveries (user_id, day_bucket);

CREATE TABLE IF NOT EXISTS free_media_pool (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    vault_item_id TEXT NOT NULL,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_free_media_pool_creator_item
        UNIQUE (creator_id, vault_item_id)
);
CREATE INDEX IF NOT EXISTS idx_free_media_pool_creator
    ON free_media_pool (creator_id, approved_at);
