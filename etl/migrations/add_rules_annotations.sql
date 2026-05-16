-- Add annotation columns to rules table
ALTER TABLE rules ADD COLUMN IF NOT EXISTS q1 TEXT;
ALTER TABLE rules ADD COLUMN IF NOT EXISTS q2 TEXT;
ALTER TABLE rules ADD COLUMN IF NOT EXISTS q3 TEXT;
ALTER TABLE rules ADD COLUMN IF NOT EXISTS ann_summary TEXT;

-- Create rule_chunks table (mirrors act_chunks)
CREATE TABLE IF NOT EXISTS rule_chunks (
    id          SERIAL PRIMARY KEY,
    rule_id     INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    sub_section_label TEXT NOT NULL,
    chunk_content     TEXT NOT NULL,
    embedding_text    TEXT,
    embedding   vector(768)
);

CREATE INDEX IF NOT EXISTS idx_rule_chunks_rule_id ON rule_chunks(rule_id);
CREATE INDEX IF NOT EXISTS idx_rule_chunks_fts
    ON rule_chunks USING gin(to_tsvector('english', chunk_content));
CREATE INDEX IF NOT EXISTS idx_rule_chunks_embedding_hnsw
    ON rule_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
