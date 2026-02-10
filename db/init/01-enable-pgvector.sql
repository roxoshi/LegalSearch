-- 1. Enable the vector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Full-text search index (Keep this if you still want keyword-based search on the full text)
CREATE INDEX idx_content_tsvector ON documents USING GIN (to_tsvector('english', content));

-- 3. HNSW Index for the NEW chunks table (Crucial for performance)
-- Note: 'embedding' here refers to the column in document_chunks
CREATE INDEX idx_chunk_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- 4. Optional: Add an index on the foreign key for faster joins
CREATE INDEX idx_chunks_doc_id ON document_chunks(document_id);

-- 5. Index on updated_at for incremental sync queries
CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at);