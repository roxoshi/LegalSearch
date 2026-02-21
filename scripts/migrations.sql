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

-- Enable uuid-ossp extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==================== Auth Schema Migration ====================
-- Migrate from old password-based users table to Identity-Provider pattern

DO $$
BEGIN
    -- If old users table exists with 'email' column, drop it
    -- (old schema: id SERIAL, email, hashed_password, name, oauth_provider, oauth_id, is_active)
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='email') THEN
        DROP TABLE IF EXISTS users CASCADE;
    END IF;
END $$;

-- Create new users table with UUID PKs
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    first_name VARCHAR(50) NOT NULL DEFAULT '',
    last_name VARCHAR(50) NOT NULL DEFAULT '',
    year_of_birth INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ck_users_year_of_birth CHECK (year_of_birth > 1900)
);

-- Create user_identities table
CREATE TABLE IF NOT EXISTS user_identities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL,
    provider_id VARCHAR(255) NOT NULL,
    is_verified BOOLEAN DEFAULT FALSE,
    UNIQUE(provider, provider_id)
);

-- Create otp_codes table
CREATE TABLE IF NOT EXISTS otp_codes (
    id SERIAL PRIMARY KEY,
    identifier VARCHAR(255) NOT NULL,
    otp_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_otp_codes_identifier ON otp_codes(identifier);
