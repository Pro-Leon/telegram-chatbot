-- P3.3 content families (creator-owned vault groupings).
-- Additive only (IF NOT EXISTS). Asserted by tests/test_p33_content_families.py.
-- Contract: docs/COMMERCE_DB_REBUILD_SPEC.md Phase 3a (DDL now; db/families.py next).

CREATE TABLE IF NOT EXISTS commerce_content_families (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_content_families_creator_slug UNIQUE (creator_id, slug),
    CONSTRAINT uq_content_families_id_creator UNIQUE (id, creator_id)
);

CREATE TABLE IF NOT EXISTS commerce_content_family_members (
    family_id BIGINT NOT NULL,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    vault_item_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_content_family_members PRIMARY KEY (family_id, vault_item_id),
    CONSTRAINT fk_content_family_members_family FOREIGN KEY (family_id, creator_id)
        REFERENCES commerce_content_families (id, creator_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_content_family_members_lookup
    ON commerce_content_family_members (creator_id, vault_item_id);
