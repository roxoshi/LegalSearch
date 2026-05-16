-- Library chunks table for notification + circular semantic search
-- Run with: psql $DATABASE_URL -f etl/migrations/add_library_chunks.sql

CREATE TABLE IF NOT EXISTS library_chunks (
    id          SERIAL PRIMARY KEY,
    doc_type    VARCHAR(20)  NOT NULL,   -- 'notification' or 'circular'
    primary_id  INTEGER      NOT NULL,
    chunk_index INTEGER      NOT NULL,
    content     TEXT         NOT NULL,
    embedding   vector(384),
    UNIQUE (doc_type, primary_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_library_chunks_lookup
    ON library_chunks (doc_type, primary_id);

CREATE INDEX IF NOT EXISTS idx_library_chunks_fts
    ON library_chunks
    USING gin (to_tsvector('english', content));

CREATE INDEX IF NOT EXISTS idx_library_chunks_embedding
    ON library_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
