#!/bin/bash
set -e

echo "=========================================="
echo "  LegalSearch - Start Services"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check Docker
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi

echo -e "\n${GREEN}[1/4] Building images...${NC}"
docker compose build

echo -e "\n${GREEN}[2/4] Starting database...${NC}"
docker compose up -d db

echo "Waiting for PostgreSQL to be ready..."
until docker compose exec -T db pg_isready -U user -d search_db > /dev/null 2>&1; do
    sleep 2
    echo "  Waiting..."
done
echo "Database ready!"

echo -e "\n${GREEN}[3/4] Running database migrations...${NC}"
# Ensure tables exist (including users table for auth)
docker compose exec -T db psql -U user -d search_db <<EOF
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Add columns if they don't exist (for existing databases)
DO \$\$
BEGIN
    -- Documents table columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='is_gst_core') THEN
        ALTER TABLE documents ADD COLUMN is_gst_core BOOLEAN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='extracted_provisions') THEN
        ALTER TABLE documents ADD COLUMN extracted_provisions TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='extracted_statutes') THEN
        ALTER TABLE documents ADD COLUMN extracted_statutes TEXT[];
    END IF;
EXCEPTION
    WHEN undefined_table THEN
        NULL; -- Table doesn't exist yet, will be created by SQLAlchemy
END \$\$;

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
EOF

echo -e "\n${GREEN}[4/4] Starting backend and frontend...${NC}"
docker compose up -d backend frontend

echo -e "\n${GREEN}=========================================="
echo "  LegalSearch is running!"
echo "==========================================${NC}"
echo ""
echo "  Frontend:  http://localhost:3000"
echo "  Backend:   http://localhost:8000"
echo "  Database:  localhost:5432"
echo ""
echo -e "${YELLOW}View logs:${NC} docker compose logs -f"
echo -e "${YELLOW}Stop:${NC} ./scripts/stop.sh"
