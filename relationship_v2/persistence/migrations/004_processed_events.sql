-- Sunny V2 Stage A2 migration: processed-event idempotency markers.
-- Additive only. Run after 003. Never touches legacy tables.
-- Contract: Phased_Plan Phase 25 + sunny_upgrade_v2 §59.
-- One row per (event_id, processor); reprocessing returns the existing row.

CREATE TABLE IF NOT EXISTS v2_processed_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id TEXT NOT NULL,
    processor TEXT NOT NULL,
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID REFERENCES v2_relationships(id) ON DELETE SET NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_v2_processed_event UNIQUE (event_id, processor)
);
CREATE INDEX IF NOT EXISTS idx_v2_processed_scope
    ON v2_processed_events (creator_id, user_id, processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_v2_processed_event
    ON v2_processed_events (event_id, processor);

-- Down migration:
-- DROP TABLE IF EXISTS v2_processed_events;
