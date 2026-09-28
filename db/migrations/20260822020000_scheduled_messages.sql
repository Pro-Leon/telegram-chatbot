-- Scheduled messages: durable outbound job queue with dedup + retries.
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260822020000_scheduled_messages.sql, original file lost 2026-09-28);
-- content verified against tests/test_scheduled_messages.py pins and live
-- staging DDL.

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT '',
    media_type TEXT,
    media_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    dedup_key TEXT,
    run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT scheduled_messages_status_check
        CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
    CONSTRAINT scheduled_messages_attempts_check
        CHECK (attempts >= 0 AND attempts <= max_attempts)
);
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_pending
    ON scheduled_messages (run_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_dedup
    ON scheduled_messages (creator_id, dedup_key) WHERE dedup_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_user
    ON scheduled_messages (creator_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_creator_run
    ON scheduled_messages (creator_id, run_at);
