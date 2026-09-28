-- P3.2 safety foundation: offer snapshots, creator-scoped uniqueness,
-- delivery dedup, crash-window intents.
-- Additive only (IF NOT EXISTS). Asserted by
-- tests/test_p32_safety_foundation.py and the migrate engine splitter test
-- (18 statements, 4 DO blocks).
-- Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3a.

ALTER TABLE commerce_offers
    ADD COLUMN IF NOT EXISTS vault_item_ids TEXT[];
ALTER TABLE commerce_offers
    ADD COLUMN IF NOT EXISTS media_count INTEGER;
ALTER TABLE commerce_offers
    ADD COLUMN IF NOT EXISTS drop_content_hash TEXT;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_commerce_offers_media_count') THEN
        ALTER TABLE commerce_offers ADD CONSTRAINT chk_commerce_offers_media_count CHECK (media_count >= 0);
    END IF;
END $$;
DROP INDEX IF EXISTS idx_commerce_offers_transaction_id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_commerce_offers_creator_txn
    ON commerce_offers (creator_id, transaction_id) WHERE transaction_id IS NOT NULL;
ALTER TABLE fangate_products
    ADD COLUMN IF NOT EXISTS dropfans_product_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_fangate_products_creator_dropfans
    ON fangate_products (creator_id, dropfans_product_id) WHERE dropfans_product_id IS NOT NULL;
DO $$
BEGIN
    DELETE FROM vault_media_deliveries
    WHERE id NOT IN (
        SELECT MIN(id) FROM vault_media_deliveries
        WHERE dropfans_vault_item_id IS NOT NULL
        GROUP BY creator_id, user_id, dropfans_vault_item_id
    );
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_deliveries_creator_user_vault
    ON vault_media_deliveries (creator_id, user_id, dropfans_vault_item_id)
    WHERE dropfans_vault_item_id IS NOT NULL;
DO $$
BEGIN
    DELETE FROM commerce_offers
    WHERE id NOT IN (
        SELECT MIN(id) FROM commerce_offers
        WHERE transaction_id IS NOT NULL
        GROUP BY creator_id, transaction_id
    );
END $$;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM commerce_offers WHERE transaction_id IS NOT NULL AND state <> 'purchased') THEN
        RAISE EXCEPTION 'P3.2: % groups map; resolve manually', 'txn-state';
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS dropfans_drop_intents (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    content_key TEXT NOT NULL,
    canonical_vault_item_ids TEXT[] NOT NULL DEFAULT '{}',
    price_minor INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    allow_download BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_drop_intents_creator_key UNIQUE (creator_id, content_key)
);
CREATE INDEX IF NOT EXISTS idx_drop_intents_creator_status
    ON dropfans_drop_intents (creator_id, status);
ALTER TABLE dropfans_drop_intents
    ADD COLUMN IF NOT EXISTS dropfans_product_id TEXT;
ALTER TABLE dropfans_drop_intents
    ADD COLUMN IF NOT EXISTS error TEXT;
CREATE INDEX IF NOT EXISTS idx_commerce_offers_snapshot_hash
    ON commerce_offers (creator_id, drop_content_hash);
CREATE INDEX IF NOT EXISTS idx_drop_intents_content_key
    ON dropfans_drop_intents (creator_id, content_key);
