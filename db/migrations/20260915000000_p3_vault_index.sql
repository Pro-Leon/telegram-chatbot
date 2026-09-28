-- P3 vault index + selection config (DropFans provider mirror).
-- Additive only (IF NOT EXISTS). Asserted by tests/test_p3_polling_vault.py.
-- Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 2.

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
