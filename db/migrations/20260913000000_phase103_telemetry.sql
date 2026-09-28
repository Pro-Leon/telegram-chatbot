-- Phase 103 telemetry columns on generation_telemetry (observability only).
-- Additive only (IF NOT EXISTS). Evidence: docs/HANDOFF.md §7.3
-- (20260913000000_phase103_telemetry.sql, original file lost 2026-09-28);
-- content verified against tests/test_phase103_telemetry.py pins and live
-- staging DDL (columns already present there).

ALTER TABLE generation_telemetry
    ADD COLUMN IF NOT EXISTS warming_level TEXT,
    ADD COLUMN IF NOT EXISTS warming_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS warming_available BOOLEAN,
    ADD COLUMN IF NOT EXISTS warming_ceiling TEXT,
    ADD COLUMN IF NOT EXISTS readiness_level TEXT,
    ADD COLUMN IF NOT EXISTS readiness_available BOOLEAN,
    ADD COLUMN IF NOT EXISTS menu_items JSONB,
    ADD COLUMN IF NOT EXISTS menu_context_chars INTEGER,
    ADD COLUMN IF NOT EXISTS free_photo_outcome TEXT,
    ADD COLUMN IF NOT EXISTS free_photo_vault_item TEXT,
    ADD COLUMN IF NOT EXISTS free_photo_llm_flag BOOLEAN,
    ADD COLUMN IF NOT EXISTS free_photo_delivery TEXT,
    ADD COLUMN IF NOT EXISTS free_photo_telegram_id BIGINT,
    ADD COLUMN IF NOT EXISTS free_photo_reservation_id BIGINT;
