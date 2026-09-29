-- Persona structured metadata (creator-scoped identities + versioning).
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260831000001_persona_structured.sql, original file lost 2026-09-28);
-- content verified against tests/test_phase43b_persona.py pins and live
-- staging DDL (all columns already present there).

ALTER TABLE personas
    ADD COLUMN IF NOT EXISTS instructions TEXT NOT NULL DEFAULT '';
ALTER TABLE personas
    ADD COLUMN IF NOT EXISTS creator_id BIGINT REFERENCES creators(id) ON DELETE CASCADE;
ALTER TABLE personas
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
ALTER TABLE personas
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE personas
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_personas_creator_default
    ON personas (creator_id, is_default) WHERE creator_id IS NOT NULL;
