#!/bin/bash
set -e

# Restore a pg_dump into the LegalSearch database
# Usage: ./scripts/db-restore.sh [environment] <dump-file>

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

if [ $# -lt 1 ]; then
    echo "Usage: $0 [environment] <dump-file>"
    echo "Example: $0 dev .data/db-dumps/legalsearch_20240101_120000.dump"
    echo "         $0 .data/db-dumps/legalsearch_20240101_120000.dump  (defaults to dev)"
    exit 1
fi

# Parse args: if two args, first is environment; if one, default to dev
if [ $# -ge 2 ]; then
    ENVIRONMENT="$1"
    DUMP_FILE="$2"
else
    ENVIRONMENT="dev"
    DUMP_FILE="$1"
fi

if [ ! -f "$DUMP_FILE" ]; then
    echo -e "${RED}Error: Dump file not found: $DUMP_FILE${NC}"
    exit 1
fi

# Source environment file
ENV_FILE="${ENV_FILE:-envs/.env.${ENVIRONMENT}}"
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
else
    echo -e "${YELLOW}Warning: $ENV_FILE not found. Using defaults.${NC}"
fi

# Use same project name as deploy.sh so we target the right containers
export COMPOSE_PROJECT_NAME="legalsearch-${ENVIRONMENT}"

# Check if db is running
if ! docker compose exec -T db pg_isready -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" > /dev/null 2>&1; then
    echo -e "${RED}Error: Database is not running. Start it first:${NC}"
    echo "  ./scripts/deploy.sh ${ENVIRONMENT} start"
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
