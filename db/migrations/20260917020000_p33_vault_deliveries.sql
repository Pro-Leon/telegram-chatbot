-- Commerce rebuild, Phase 3b: vault_media_deliveries ledger DDL.
-- Additive only (IF NOT EXISTS). Evidence: live staging DDL
-- (information_schema + pg_indexes, 2026-09-28); docs/HANDOFF.md:476
-- (20260823010000_vault_media.sql, file since lost); call sites in
-- chatbotv2/main.py, vault/service.py, commerce/post_purchase.py.
-- No modification of existing tables.

CREATE TABLE IF NOT EXISTS vault_media_deliveries (
    id BIGSERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL,
    user_id BIGINT NOT NULL,
    fangate_media_id INTEGER,
    product_id INTEGER,
    telegram_message_id BIGINT,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'sent',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dropfans_media_id TEXT,
    dropfans_vault_item_id TEXT,
    UNIQUE (creator_id, user_id, fangate_media_id)
);

CREATE INDEX IF NOT EXISTS idx_vault_deliveries_user
    ON vault_media_deliveries (user_id, fangate_media_id);

CREATE INDEX IF NOT EXISTS idx_vault_deliveries_creator
    ON vault_media_deliveries (creator_id, user_id);

CREATE INDEX IF NOT EXISTS idx_vault_deliveries_pending_stale
    ON vault_media_deliveries (created_at)
    WHERE (status = 'pending');

CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_deliveries_creator_user_vault
    ON vault_media_deliveries (creator_id, user_id, dropfans_vault_item_id)
    WHERE (dropfans_vault_item_id IS NOT NULL);
