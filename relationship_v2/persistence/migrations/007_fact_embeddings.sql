-- Sunny V2 Stage F5 migration: semantic embeddings for memory facts.
-- Additive only. Run after 006. Requires the pgvector extension
-- (already required by db/schema.sql for message_embeddings).
-- Contract: MEMORY_ARCHITECTURE.md hybrid lexical + semantic retrieval.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE v2_memory_facts
    ADD COLUMN IF NOT EXISTS embedding vector(1536);

CREATE INDEX IF NOT EXISTS idx_v2_facts_embedding
    ON v2_memory_facts USING hnsw (embedding vector_cosine_ops);

-- Down migration:
-- DROP INDEX IF EXISTS idx_v2_facts_embedding;
-- ALTER TABLE v2_memory_facts DROP COLUMN IF EXISTS embedding;
