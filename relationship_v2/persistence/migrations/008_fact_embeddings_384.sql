-- Sunny V2 Stage F5 follow-up: align fact embeddings with MiniLM (384).
-- Additive-safe: rewrites the still-unpopulated v2_memory_facts.embedding
-- column in place. Run after 007. Legacy message/user embedding columns
-- (PRESERVED, 1536) are untouched.
--
-- Contract: MEMORY_ARCHITECTURE.md hybrid retrieval; embedder behind
-- relationship_v2/integration/minilm.py (local sentence-transformers).

ALTER TABLE v2_memory_facts
    ALTER COLUMN embedding TYPE vector(384) USING embedding::vector(384);

-- Down migration:
-- ALTER TABLE v2_memory_facts
--     ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536);
