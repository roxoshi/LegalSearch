-- LegalSearch Database Migrations (idempotent)
-- Run via: psql -U $POSTGRES_USER -d $POSTGRES_DB < scripts/migrations.sql

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Add ML columns if they don't exist (for existing databases)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='is_gst_core') THEN
        ALTER TABLE documents ADD COLUMN is_gst_core BOOLEAN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='extracted_provisions') THEN
        ALTER TABLE documents ADD COLUMN extracted_provisions TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='extracted_statutes') THEN
        ALTER TABLE documents ADD COLUMN extracted_statutes TEXT[];
    END IF;

    -- Timestamp columns for sync support
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='created_at') THEN
        ALTER TABLE documents ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='updated_at') THEN
        ALTER TABLE documents ADD COLUMN updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL;
    END IF;
EXCEPTION
    WHEN undefined_table THEN
        NULL; -- Table doesn't exist yet, will be created by SQLAlchemy
END $$;

-- Trigger function to auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach trigger to documents table (idempotent via DROP IF EXISTS)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='documents') THEN
        DROP TRIGGER IF EXISTS set_updated_at ON documents;
        CREATE TRIGGER set_updated_at
            BEFORE UPDATE ON documents
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- Index on updated_at for incremental sync queries
CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at);

-- Create users table for authentication
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT,
    name TEXT,
    oauth_provider TEXT,
    oauth_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
