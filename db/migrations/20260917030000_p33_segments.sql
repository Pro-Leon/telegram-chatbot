-- Commerce rebuild, Phase 3c: fan_segments DDL. Additive only (IF NOT EXISTS).
-- Evidence: live staging DDL (information_schema + pg_indexes, 2026-09-28);
-- docs/PHASE_5_1_IMPLEMENTATION_MAP.md:704-725
-- (20260823030000_fan_segments.sql, file since lost).
-- No modification of existing tables.

CREATE TABLE IF NOT EXISTS fan_segments (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    rules JSONB NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    member_count INTEGER NOT NULL DEFAULT 0,
    last_evaluated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fan_segments_creator
    ON fan_segments (creator_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fan_segments_creator_name
    ON fan_segments (creator_id, LOWER(name));
