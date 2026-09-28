-- Sunny V2 Phase 2 migration: relationship snapshots (append-only audit).
-- Additive only. Run after Phase 1 schema.

CREATE TABLE IF NOT EXISTS v2_relationship_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    lifecycle TEXT NOT NULL
        CHECK (lifecycle IN ('new','warming','established','deep','dormant','reactivated')),
    version BIGINT NOT NULL CHECK (version >= 1),
    as_of TIMESTAMPTZ NOT NULL,
    absence_days INTEGER CHECK (absence_days IS NULL OR absence_days >= 0),
    evidence_hash TEXT NOT NULL,
    snapshot JSONB NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_v2_snapshots_scope
    ON v2_relationship_snapshots (creator_id, user_id, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_v2_snapshots_relationship
    ON v2_relationship_snapshots (relationship_id, as_of DESC);

-- Down migration:
-- DROP TABLE IF EXISTS v2_relationship_snapshots;
