-- Baseline schema (legacy core tables). Additive only (IF NOT EXISTS).
-- Evidence: live staging DDL (information_schema, 2026-09-28);
-- tests/test_database_migrations.py Group G (required tables/indexes/seed).
-- Recreates the lost 00000000000000_baseline.sql surface: same tables,
-- indexes, and seed verified live. No destructive statements.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    message_count INTEGER DEFAULT 0,
    funnel_stage TEXT DEFAULT 'new',
    is_blocked BOOLEAN DEFAULT FALSE,
    notes TEXT,
    persona_id INTEGER,
    do_not_auto_reply BOOLEAN DEFAULT FALSE,
    first_purchase_at TIMESTAMPTZ,
    first_offer_id BIGINT,
    first_transaction_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_name_trgm
    ON users USING gin (username gin_trgm_ops);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    direction TEXT NOT NULL,
    content TEXT NOT NULL,
    telegram_message_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_user_id_created
    ON messages (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_content_trgm
    ON messages USING gin (content gin_trgm_ops);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_summaries_user_id
    ON conversation_summaries (user_id);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    facts JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS message_embeddings (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT REFERENCES messages(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    embedding TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_message_embeddings_user
    ON message_embeddings (user_id);
CREATE INDEX IF NOT EXISTS idx_message_embeddings_vector
    ON message_embeddings (user_id, id);

CREATE TABLE IF NOT EXISTS personas (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_personas_default
    ON personas (is_default) WHERE is_default;
INSERT INTO personas (name, display_name, is_default) VALUES
    ('sunny', 'Sunny', TRUE)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS operator_queue (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    draft_content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_operator_queue_status
    ON operator_queue (status, created_at);

CREATE TABLE IF NOT EXISTS operators (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE,
    username TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    data JSONB NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires
    ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS conversation_attention (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_attention_status
    ON conversation_attention (status, user_id);

CREATE TABLE IF NOT EXISTS conversation_notes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_notes_user_created
    ON conversation_notes (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conv_notes_content_trgm
    ON conversation_notes USING gin (content gin_trgm_ops);

CREATE TABLE IF NOT EXISTS dlq_messages (
    id BIGSERIAL PRIMARY KEY,
    stream TEXT NOT NULL,
    message_id TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_tags (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversation_tags_name_lower
    ON conversation_tags (LOWER(name));

CREATE TABLE IF NOT EXISTS conversation_tag_assignments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag_id BIGINT NOT NULL REFERENCES conversation_tags(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_tag_assign_user
    ON conversation_tag_assignments (user_id);
CREATE INDEX IF NOT EXISTS idx_conv_tag_assign_tag
    ON conversation_tag_assignments (tag_id);
