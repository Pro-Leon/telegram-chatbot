-- Extensions required
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users (fans)
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    message_count INTEGER DEFAULT 0,
    funnel_stage TEXT DEFAULT 'new',
    is_blocked BOOLEAN DEFAULT FALSE,
    do_not_auto_reply BOOLEAN DEFAULT FALSE,
    notes TEXT
);

-- All messages (full audit log)
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    direction TEXT NOT NULL,
    content TEXT NOT NULL,
    draft_content TEXT,
    was_edited BOOLEAN DEFAULT FALSE,
    was_auto_approved BOOLEAN DEFAULT FALSE,
    confidence_score FLOAT,
    operator_id BIGINT,
    telegram_message_id INTEGER,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_user_id_created
    ON messages(user_id, created_at DESC);

-- Conversation summaries (rolling)
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    summary TEXT NOT NULL,
    message_count_at_summary INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_summaries_user_id
    ON conversation_summaries(user_id);

-- User profiles (structured facts)
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    facts JSONB DEFAULT '{}',
    embedding vector(1536),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Message embeddings (for history retrieval)
CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id BIGINT PRIMARY KEY REFERENCES messages(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_embeddings_user
    ON message_embeddings(user_id);

CREATE INDEX IF NOT EXISTS idx_message_embeddings_vector
    ON message_embeddings USING hnsw (embedding vector_cosine_ops);

-- Operator queue
CREATE TABLE IF NOT EXISTS operator_queue (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    draft_content TEXT NOT NULL,
    confidence_score FLOAT,
    flags JSONB DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    assigned_to BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_operator_queue_status
    ON operator_queue(status, created_at);

-- Operators
CREATE TABLE IF NOT EXISTS operators (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE,
    username TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Personas (conversational identities, creator-scoped)
CREATE TABLE IF NOT EXISTS personas (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    instructions TEXT NOT NULL DEFAULT '',
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    creator_id BIGINT REFERENCES creators(id) ON DELETE CASCADE,
    metadata JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Dead letter queue for failed messages
CREATE TABLE IF NOT EXISTS dlq_messages (
    id BIGSERIAL PRIMARY KEY,
    original_stream_id TEXT,
    message_data JSONB,
    failure_reason TEXT,
    enqueued_at TIMESTAMPTZ DEFAULT NOW(),
    attempts INTEGER DEFAULT 1
);

-- Conversation tags (no color column by contract)
CREATE TABLE IF NOT EXISTS conversation_tags (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_tag_assignments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag_id BIGINT NOT NULL REFERENCES conversation_tags(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, tag_id)
);
