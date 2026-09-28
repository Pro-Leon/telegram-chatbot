-- P3.2 crash-window intents (DropFans create-post convergence).
-- Additive only (IF NOT EXISTS). Asserted by tests/test_p32_safety_foundation.py.
-- Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3a.

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
    dropfans_product_id TEXT,
    error TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_drop_intents_creator_key UNIQUE (creator_id, content_key)
);
CREATE INDEX IF NOT EXISTS idx_drop_intents_creator_status
    ON dropfans_drop_intents (creator_id, status);
