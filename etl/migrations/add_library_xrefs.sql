-- Migration: add library_xrefs table for withdrawal/supersession relationships
-- Run once: psql $DATABASE_URL -f etl/migrations/add_library_xrefs.sql

DO $$ BEGIN
    CREATE TYPE xrefrelationship AS ENUM ('WITHDRAWS', 'SUPERSEDES', 'AMENDS');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS library_xrefs (
    id              SERIAL PRIMARY KEY,
    from_doc_type   doctype         NOT NULL,
    from_primary_id INTEGER         NOT NULL,
    to_doc_type     doctype         NOT NULL,
    to_primary_id   INTEGER         NOT NULL,
    relationship    xrefrelationship NOT NULL,
    ab_initio       BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT uq_library_xref UNIQUE (from_doc_type, from_primary_id, to_doc_type, to_primary_id)
);

CREATE INDEX IF NOT EXISTS idx_lxref_from ON library_xrefs (from_doc_type, from_primary_id);
CREATE INDEX IF NOT EXISTS idx_lxref_to   ON library_xrefs (to_doc_type,   to_primary_id);
