-- Migration: Add embedding column + HNSW index to acts table
-- Run with: psql $DATABASE_URL -f etl/migrations/add_acts_embedding.sql
--
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
-- psql runs each statement outside a transaction by default, so this is safe.

ALTER TABLE acts ADD COLUMN IF NOT EXISTS embedding vector(384);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_acts_embedding_hnsw
    ON acts
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
