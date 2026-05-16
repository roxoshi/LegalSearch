-- Migration: Add GIN full-text index on document_chunks.chunk_content
-- Run with: psql $DATABASE_URL -f etl/migrations/add_chunks_fts_index.sql
--
-- CREATE INDEX CONCURRENTLY does not lock the table for reads or writes,
-- but it CANNOT run inside a transaction block. This file must be executed
-- outside of a transaction (psql does this by default).

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_fts
    ON document_chunks
    USING gin (to_tsvector('english', chunk_content));
