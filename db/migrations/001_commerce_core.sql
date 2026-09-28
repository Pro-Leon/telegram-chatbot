-- Commerce core rebuild, Phase 1: creators, integrations, products,
-- transactions, wallet, webhook events. Additive only (IF NOT EXISTS).
-- Evidence: docs/COMMERCE_DB_REBUILD_SPEC.md (call-site + test + docs
-- extraction). No modification of existing tables.

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

CREATE TABLE IF NOT EXISTS fangate_transactions (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    transaction_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    buyer_email TEXT,
    buyer_external_id TEXT,
    seller_earning NUMERIC(12,2),
    currency TEXT,
    product_id BIGINT,
    set_price NUMERIC,
    occurred_at TIMESTAMPTZ,
    delivery_id TEXT,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fangate_transactions_identity
        UNIQUE (creator_id, transaction_id, event_type)
);
CREATE INDEX IF NOT EXISTS idx_fangate_transactions_user
    ON fangate_transactions (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fangate_transactions_creator
    ON fangate_transactions (creator_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fangate_wallet_entries (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    wallet_tx_id BIGINT NOT NULL,
    amount_minor INTEGER,
    txn_type TEXT,
    created_at TEXT,
    title TEXT,
    raw JSONB NOT NULL DEFAULT '{}',
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fangate_wallet_identity UNIQUE (creator_id, wallet_tx_id)
);

CREATE TABLE IF NOT EXISTS fangate_webhook_events (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    delivery_id TEXT NOT NULL,
    event TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    signature_valid BOOLEAN NOT NULL DEFAULT FALSE,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fangate_webhook_identity UNIQUE (creator_id, delivery_id)
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_creator_processed
    ON fangate_webhook_events (creator_id, processed);
