-- Commerce rebuild Phase 2: DropFans canonical CUID column + vault tables.
-- Additive only (IF NOT EXISTS). Asserted shape: p32 tests require
-- ON CONFLICT (creator_id, dropfans_product_id) in upsert_dropfans_product.

ALTER TABLE fangate_products
    ADD COLUMN IF NOT EXISTS dropfans_product_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_fangate_products_dropfans_cuid'
    ) THEN
        -- Plain UNIQUE: NULL cuids stay distinct, CUID rows arbitrate.
        ALTER TABLE fangate_products
            ADD CONSTRAINT uq_fangate_products_dropfans_cuid
            UNIQUE (creator_id, dropfans_product_id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS dropfans_vault_index (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    vault_item_id TEXT NOT NULL,
    content_tags TEXT[] NOT NULL DEFAULT '{}',
    folder_id TEXT,
    folder_name TEXT,
    moderation_status TEXT,
    file_type TEXT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dropfans_vault_item UNIQUE (creator_id, vault_item_id)
);
CREATE INDEX IF NOT EXISTS idx_dropfans_vault_creator
    ON dropfans_vault_index (creator_id, vault_item_id);

CREATE TABLE IF NOT EXISTS dropfans_selection_config (
    creator_id BIGINT PRIMARY KEY REFERENCES creators(id) ON DELETE CASCADE,
    allowed_folders TEXT[] NOT NULL DEFAULT '{}',
    allowed_tags TEXT[] NOT NULL DEFAULT '{}',
    hard_mode BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
