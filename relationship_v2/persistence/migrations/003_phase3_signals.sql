-- Sunny V2 Phase 3 migration: engagement signals (evidence with counts).
-- Additive only. Run after 002.

CREATE TABLE IF NOT EXISTS v2_engagement_signals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    behavior TEXT NOT NULL,
    polarity TEXT NOT NULL DEFAULT 'neutral'
        CHECK (polarity IN ('positive','negative','neutral')),
    evidence_count INTEGER NOT NULL DEFAULT 1 CHECK (evidence_count >= 1),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    first_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_v2_signals_topic UNIQUE (creator_id, user_id, topic, behavior)
);
CREATE INDEX IF NOT EXISTS idx_v2_signals_scope
    ON v2_engagement_signals (creator_id, user_id, last_observed_at DESC);

-- Down migration:
-- DROP TABLE IF EXISTS v2_engagement_signals;
