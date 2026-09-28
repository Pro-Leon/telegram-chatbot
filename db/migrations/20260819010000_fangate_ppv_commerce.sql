-- Fangate PPV commerce: offers ledger + fan attribution on transactions.
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260819010000_fangate_ppv_commerce.sql, original file lost 2026-09-28);
-- content verified against tests/test_commerce_domain.py pins and live
-- staging DDL; ledger/transaction tables consistent with
-- db/migrations/001_commerce_core.sql.

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fangate_transactions_identity
        UNIQUE (creator_id, transaction_id, event_type)
);
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

CREATE TABLE IF NOT EXISTS commerce_offers (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id BIGINT,
    link TEXT,
    transaction_id TEXT,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'clicked', 'purchased', 'declined', 'expired', 'revoked')),
    price_minor INTEGER CHECK (price_minor IS NULL OR price_minor >= 0),
    reason TEXT,
    created_by TEXT,
    expires_at TIMESTAMPTZ,
    clicked_at TIMESTAMPTZ,
    purchased_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (purchased_at IS NULL OR state = 'purchased'),
    CHECK (transaction_id IS NULL OR state = 'purchased')
);
CREATE INDEX IF NOT EXISTS idx_commerce_offers_user_state
    ON commerce_offers (user_id, state);
CREATE INDEX IF NOT EXISTS idx_commerce_offers_creator_product_state
    ON commerce_offers (creator_id, product_id, state);
CREATE INDEX IF NOT EXISTS idx_commerce_offers_creator_user_created
    ON commerce_offers (creator_id, user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_commerce_offers_transaction_id
    ON commerce_offers (transaction_id) WHERE transaction_id IS NOT NULL;

ALTER TABLE fangate_transactions
    ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_fangate_transactions_user
    ON fangate_transactions (user_id) WHERE user_id IS NOT NULL;
