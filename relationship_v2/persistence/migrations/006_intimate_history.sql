-- Sunny V2 Stage C3 migration: intimate history (semantic abstractions).
-- Additive only. Run after 005. Never touches legacy tables.
-- Contract: Phased_Plan Phase 11 + sunny_upgrade_v2 §16 + 05_MEMORY_SPEC.
-- Abstract signals only; no raw-content column exists by design.

CREATE TABLE IF NOT EXISTS v2_intimate_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK (kind IN ('comfort','preference','boundary','turnoff','milestone')),
    signal TEXT NOT NULL CHECK (char_length(signal) BETWEEN 1 AND 280),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    importance TEXT NOT NULL DEFAULT 'normal'
        CHECK (importance IN ('critical','high','normal','low')),
    status TEXT NOT NULL DEFAULT 'current'
        CHECK (status IN ('current','superseded','archived')),
    supersedes_id UUID REFERENCES v2_intimate_history(id) ON DELETE SET NULL,
    provenance TEXT NOT NULL,
    source_event_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_intimate_current
    ON v2_intimate_history (relationship_id, kind, signal)
    WHERE status = 'current';
CREATE INDEX IF NOT EXISTS idx_v2_intimate_scope
    ON v2_intimate_history (creator_id, user_id, updated_at DESC);

-- Down migration:
-- DROP TABLE IF EXISTS v2_intimate_history;
