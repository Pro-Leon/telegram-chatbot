-- Sunny V2 Phase 1 migration: core domain persistence.
-- Additive only. No modification of existing tables. No commerce writes.
-- Owners: see relationship_v2/persistence/owners.py (one owner per table).

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Relationships: one row per (creator_id, user_id).
CREATE TABLE IF NOT EXISTS v2_relationships (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    lifecycle TEXT NOT NULL DEFAULT 'new'
        CHECK (lifecycle IN ('new','warming','established','deep','dormant','reactivated')),
    familiarity TEXT NOT NULL DEFAULT 'stranger',
    comfort TEXT NOT NULL DEFAULT 'low',
    escalation_stage TEXT NOT NULL DEFAULT 'relationship',
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    first_interaction_at TIMESTAMPTZ,
    last_interaction_at TIMESTAMPTZ,
    provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_v2_relationships_scope UNIQUE (creator_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_v2_relationships_updated
    ON v2_relationships (creator_id, user_id, updated_at DESC);

-- 2. Memory facts: temporal, append-oriented. History via supersession rows.
CREATE TABLE IF NOT EXISTS v2_memory_facts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    value TEXT NOT NULL,
    previous_value TEXT,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate','validated','current','superseded','archived')),
    importance TEXT NOT NULL DEFAULT 'normal'
        CHECK (importance IN ('critical','high','normal','low')),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    effective_from TIMESTAMPTZ NOT NULL,
    effective_to TIMESTAMPTZ,
    supersedes_id UUID REFERENCES v2_memory_facts(id) ON DELETE SET NULL,
    provenance TEXT NOT NULL,
    source_event_id TEXT,
    source_message_id TEXT,
    generation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_v2_facts_temporal CHECK (effective_to IS NULL OR effective_to > effective_from)
);
CREATE INDEX IF NOT EXISTS idx_v2_facts_scope
    ON v2_memory_facts (creator_id, user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_v2_facts_current
    ON v2_memory_facts (creator_id, user_id, memory_key)
    WHERE status = 'current';
CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_facts_current_key
    ON v2_memory_facts (creator_id, user_id, memory_key)
    WHERE status = 'current';

-- Stage F5: semantic embedding for hybrid retrieval (pgvector; nullable
-- until the pipeline backfills; lexical scoring never depends on it).
-- Existing databases apply migrations/007_fact_embeddings.sql instead.
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE v2_memory_facts
    ADD COLUMN IF NOT EXISTS embedding vector(384);
CREATE INDEX IF NOT EXISTS idx_v2_facts_embedding
    ON v2_memory_facts USING hnsw (embedding vector_cosine_ops);

-- 3. Memory episodes: append-only.
CREATE TABLE IF NOT EXISTS v2_memory_episodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    episode_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    salience DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (salience >= 0 AND salience <= 1),
    generation_id TEXT,
    conversation_id UUID,
    provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_v2_episodes_scope
    ON v2_memory_episodes (creator_id, user_id, created_at DESC);

-- 4. Conversations: resumable sessions.
CREATE TABLE IF NOT EXISTS v2_conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    lifecycle TEXT NOT NULL DEFAULT 'started'
        CHECK (lifecycle IN ('started','active','paused','resumed','closed','failed')),
    topic TEXT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    provenance TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_v2_conversations_scope
    ON v2_conversations (creator_id, user_id, last_activity_at DESC);

-- 5. Conversation turns: correlation references only, no raw fan text.
CREATE TABLE IF NOT EXISTS v2_conversation_turns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES v2_conversations(id) ON DELETE CASCADE,
    generation_id TEXT NOT NULL,
    inbound_event_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    validation_verdict TEXT NOT NULL,
    routing_decision TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_v2_turns_generation UNIQUE (conversation_id, generation_id)
);
CREATE INDEX IF NOT EXISTS idx_v2_turns_conversation
    ON v2_conversation_turns (conversation_id, created_at DESC);

-- 6. Domain events: durable envelope with idempotency key.
CREATE TABLE IF NOT EXISTS v2_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID REFERENCES v2_relationships(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES v2_conversations(id) ON DELETE SET NULL,
    generation_id TEXT,
    idempotency_key TEXT NOT NULL,
    producer TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_v2_events_idem UNIQUE (creator_id, user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_v2_events_scope
    ON v2_events (creator_id, user_id, created_at DESC);

-- 7. Commerce refs: cached CONFIRMATIONS only. Never authored truth.
CREATE TABLE IF NOT EXISTS v2_commerce_refs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id BIGINT NOT NULL CHECK (creator_id > 0),
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    relationship_id UUID NOT NULL REFERENCES v2_relationships(id) ON DELETE CASCADE,
    commerce_request_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    confirmed_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_v2_commerce_req UNIQUE (creator_id, user_id, commerce_request_id)
);

-- 8. Relationship snapshots: append-only audit of derived state (Phase 2).
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

-- 9. Engagement signals: evidence with counts, never one-event preferences (Phase 3).
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

-- 10. Processed events: idempotency markers, one row per (event_id, processor).
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

-- 11. Open loops: durable conversational threads (Stage C1).
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

-- 12. Intimate history: semantic abstractions only (Stage C3, no raw content).
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

-- Down migration (rollback, reverse order):
-- DROP TABLE IF EXISTS v2_intimate_history;
-- DROP TABLE IF EXISTS v2_open_loops;
-- DROP TABLE IF EXISTS v2_processed_events;
-- DROP TABLE IF EXISTS v2_engagement_signals;
-- DROP TABLE IF EXISTS v2_relationship_snapshots;
-- DROP TABLE IF EXISTS v2_commerce_refs;
-- DROP TABLE IF EXISTS v2_events;
-- DROP TABLE IF EXISTS v2_conversation_turns;
-- DROP TABLE IF EXISTS v2_conversations;
-- DROP TABLE IF EXISTS v2_memory_episodes;
-- DROP TABLE IF EXISTS v2_memory_facts;
-- DROP TABLE IF EXISTS v2_relationships;
