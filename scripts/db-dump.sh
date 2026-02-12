#!/bin/bash
set -e

# Create a pg_dump of the LegalSearch database
# Saves to .data/db-dumps/ with timestamp, keeps last 5 dumps
# Usage: ./scripts/db-dump.sh [environment]  (defaults to staging)

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ENVIRONMENT="${1:-staging}"

# Source environment file
ENV_FILE="${ENV_FILE:-envs/.env.${ENVIRONMENT}}"
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
else
    echo -e "${RED}Error: $ENV_FILE not found.${NC}"
    exit 1
fi

# Use same project name as deploy.sh so we target the right containers
export COMPOSE_PROJECT_NAME="legalsearch-${ENVIRONMENT}"

DUMP_DIR="${SYNC_DUMP_DIR:-.data/db-dumps}"
mkdir -p "$DUMP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="${DUMP_DIR}/legalsearch_${TIMESTAMP}.dump"

echo -e "${GREEN}Creating database dump...${NC}"

# Check if db is running
if ! docker compose exec -T db pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB:-search_db}" > /dev/null 2>&1; then
    echo -e "${RED}Error: Database is not running. Start it first:${NC}"
    echo "  ./scripts/deploy.sh ${ENVIRONMENT} start"
    exit 1
fi

# Show current row counts
echo -e "${YELLOW}Current database state:${NC}"
docker compose exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB:-search_db}" -c \
    "SELECT 'documents' AS table_name, COUNT(*) FROM documents UNION ALL SELECT 'document_chunks', COUNT(*) FROM document_chunks;" 2>/dev/null || true

# Run pg_dump via docker
docker compose exec -T db pg_dump \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB:-search_db}" \
    --format=custom \
    --compress=9 \
    > "$DUMP_FILE"

DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo -e "${GREEN}Dump created: $DUMP_FILE ($DUMP_SIZE)${NC}"

# Keep only last 5 dumps
DUMP_COUNT=$(ls -1 "${DUMP_DIR}"/legalsearch_*.dump 2>/dev/null | wc -l)
if [ "$DUMP_COUNT" -gt 5 ]; then
    echo -e "${YELLOW}Cleaning old dumps (keeping last 5)...${NC}"
    ls -1t "${DUMP_DIR}"/legalsearch_*.dump | tail -n +6 | xargs rm -f
fi

echo -e "${GREEN}Done.${NC}"
