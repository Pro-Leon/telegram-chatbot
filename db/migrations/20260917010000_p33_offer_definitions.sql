-- P3.3 offer definitions + drop mappings (deterministic offer catalog).
-- Additive only (IF NOT EXISTS). Asserted by tests/test_p33_offer_definitions.py.
-- Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3a.

CREATE TABLE IF NOT EXISTS commerce_offer_definitions (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    offer_type TEXT NOT NULL CHECK (offer_type IN ('SINGLE', 'SMALL_BUNDLE', 'CORE_BUNDLE', 'PREMIUM')),
    canonical_vault_item_ids TEXT[] NOT NULL
        CHECK (cardinality(canonical_vault_item_ids) BETWEEN 1 AND 10),
    family_id BIGINT,
    price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    allow_download BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
    config JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_offer_definitions_key_version UNIQUE (creator_id, stable_key, version),
    CONSTRAINT fk_offer_definitions_family FOREIGN KEY (family_id, creator_id)
        REFERENCES commerce_content_families (id, creator_id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_offer_definitions_creator_key_active
    ON commerce_offer_definitions (creator_id, stable_key) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_offer_definitions_creator
    ON commerce_offer_definitions (creator_id, status);

CREATE TABLE IF NOT EXISTS commerce_offer_definition_drops (
    definition_id BIGINT NOT NULL,
    definition_version INTEGER NOT NULL DEFAULT 1,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    dropfans_product_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_offer_definition_drops PRIMARY KEY (creator_id, dropfans_product_id),
    CONSTRAINT fk_offer_definition_drops_definition
        FOREIGN KEY (definition_id, creator_id)
        REFERENCES commerce_offer_definitions (id, creator_id) ON DELETE CASCADE
);
