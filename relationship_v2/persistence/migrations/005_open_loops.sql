-- Sunny V2 Stage C1 migration: open loops (durable conversational threads).
-- Additive only. Run after 004. Never touches legacy tables.
-- Contract: Phased_Plan Phase 8 + sunny_upgrade_v2 §13.

CREATE TABLE IF NOT EXISTS v2_open_loops (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    source_memory_id UUID REFERENCES v2_memory_facts(id) ON DELETE SET NULL,
    source_event_id TEXT,
    description TEXT NOT NULL CHECK (char_length(description) BETWEEN 1 AND 512),
    expected_at TIMESTAMPTZ,
    priority TEXT NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low','normal','high')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','due','referenced','resolved','expired','dismissed')),
    last_referenced_at TIMESTAMPTZ,
    follow_up_attempts INTEGER NOT NULL DEFAULT 0 CHECK (follow_up_attempts >= 0),
    resolved_at TIMESTAMPTZ,
    outcome TEXT,
    provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_v2_loops_scope
    ON v2_open_loops (creator_id, user_id, status, expected_at);
CREATE INDEX IF NOT EXISTS idx_v2_loops_relationship
    ON v2_open_loops (relationship_id, status);

-- Down migration:
-- DROP TABLE IF EXISTS v2_open_loops;
