-- Fangate commerce foundation: creators, integrations, product mirror.
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260819000000_fangate_commerce.sql, original file lost 2026-09-28);
-- definitions verified against live staging DDL; table-for-table
-- consistent with db/migrations/001_commerce_core.sql.
-- FROZEN: tests/test_commerce_domain.py pins this file's SHA256 —
-- re-baselined after loss; content verified against live staging.

CREATE TABLE IF NOT EXISTS creators (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS creator_integrations (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL UNIQUE REFERENCES creators(id) ON DELETE CASCADE,
    fangate_account_id TEXT,
    encrypted_api_key TEXT,
    api_key_name TEXT,
    webhook_id BIGINT,
    encrypted_webhook_secret TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    currency_code TEXT,
    dropfans_creator_id TEXT,
    dropfans_username TEXT,
    dropfans_display_name TEXT,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_creator_integrations_status
    ON creator_integrations (status, creator_id);

CREATE TABLE IF NOT EXISTS fangate_products (
    id BIGINT PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    product_type TEXT,
    title TEXT,
    preview TEXT,
    preview_blurred TEXT,
    price_minor INTEGER CHECK (price_minor IS NULL OR price_minor >= 0),
    in_collection BOOLEAN DEFAULT FALSE,
    link TEXT,
    sales_url TEXT,
    link_clicks INTEGER,
    unlocks INTEGER,
    total_earnings BIGINT,
    folder_id TEXT,
    folder JSONB,
    media JSONB,
    is_adult_content BOOLEAN DEFAULT FALSE,
    is_verif_age BOOLEAN DEFAULT FALSE,
    is_epoch_enabled BOOLEAN DEFAULT FALSE,
    is_should_consent BOOLEAN DEFAULT FALSE,
    is_downloadable BOOLEAN DEFAULT FALSE,
    is_accessible BOOLEAN DEFAULT FALSE,
    private_description TEXT,
    public_description TEXT,
    raw JSONB NOT NULL DEFAULT '{}',
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fangate_products_creator
    ON fangate_products (creator_id, id);
