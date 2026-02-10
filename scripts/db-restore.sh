#!/bin/bash
set -e

# Restore a pg_dump into the LegalSearch database
# Usage: ./scripts/db-restore.sh <dump-file>

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

if [ $# -lt 1 ]; then
    echo "Usage: $0 <dump-file>"
    echo "Example: $0 .data/db-dumps/legalsearch_20240101_120000.dump"
    exit 1
fi

DUMP_FILE="$1"

if [ ! -f "$DUMP_FILE" ]; then
    echo -e "${RED}Error: Dump file not found: $DUMP_FILE${NC}"
    exit 1
fi

# Source environment file
ENV_FILE="${ENV_FILE:-envs/.env.dev}"
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
else
    echo -e "${YELLOW}Warning: $ENV_FILE not found. Using defaults.${NC}"
fi

# Check if db is running
if ! docker compose exec -T db pg_isready -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" > /dev/null 2>&1; then
    echo -e "${RED}Error: Database is not running. Start it first:${NC}"
    echo "  docker compose up -d db"
    exit 1
fi

DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo -e "${YELLOW}Restoring $DUMP_FILE ($DUMP_SIZE)...${NC}"
echo -e "${YELLOW}This will drop and recreate all tables.${NC}"
read -p "Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# Drop existing tables
echo -e "${YELLOW}Dropping existing tables...${NC}"
docker compose exec -T db psql -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" -c \
    "DROP TABLE IF EXISTS document_chunks CASCADE; DROP TABLE IF EXISTS documents CASCADE; DROP TABLE IF EXISTS users CASCADE;"

# Restore from dump
echo -e "${GREEN}Restoring database...${NC}"
docker compose exec -T db pg_restore \
    -U "${POSTGRES_USER:-user}" \
    -d "${POSTGRES_DB:-search_db}" \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    < "$DUMP_FILE" 2>/dev/null || true  # pg_restore returns non-zero on warnings

# Run migrations to ensure latest schema
echo -e "${GREEN}Running migrations...${NC}"
docker compose exec -T db psql -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" < scripts/migrations.sql

# Show row counts
echo -e "\n${GREEN}Restore complete. Row counts:${NC}"
docker compose exec -T db psql -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" -c \
    "SELECT 'documents' AS table_name, COUNT(*) FROM documents UNION ALL SELECT 'document_chunks', COUNT(*) FROM document_chunks;"
