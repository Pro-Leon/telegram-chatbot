-- Commerce rebuild, Phase 3d: automation_operations ledger DDL.
-- Additive only (IF NOT EXISTS). Evidence: live staging DDL
-- (information_schema + pg_indexes, 2026-09-28); docs/HANDOFF.md:481
-- (20260826000000_automation_operations.sql, file since lost).
-- No modification of existing tables.

CREATE TABLE IF NOT EXISTS automation_operations (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    params JSONB NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ,
    provider_result JSONB NOT NULL DEFAULT '{}',
    error_class TEXT,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_ops_idempotency
    ON automation_operations (creator_id, idempotency_key)
    WHERE (idempotency_key <> '');

CREATE INDEX IF NOT EXISTS idx_automation_ops_status_next
    ON automation_operations (status, next_attempt_at)
    WHERE (status IN ('pending', 'retrying'));

CREATE INDEX IF NOT EXISTS idx_automation_ops_creator_created
    ON automation_operations (creator_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_automation_ops_status
    ON automation_operations (status);
