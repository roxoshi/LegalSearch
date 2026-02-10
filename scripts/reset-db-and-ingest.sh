#!/bin/bash
set -e

echo "=========================================="
echo "  Reset Database & Run Fast Ingest"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Source environment file
ENV_FILE="${ENV_FILE:-envs/.env.staging}"
if [ -f "$ENV_FILE" ]; then
    echo "Loading environment from $ENV_FILE"
    set -a; source "$ENV_FILE"; set +a
else
    echo -e "${YELLOW}Warning: $ENV_FILE not found. Using defaults or existing environment.${NC}"
fi

# Check Docker
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running.${NC}"
    exit 1
fi

# Step 1: Stop database
echo -e "\n${YELLOW}[1/4] Stopping database...${NC}"
docker compose stop db 2>/dev/null || true
docker compose rm -f db 2>/dev/null || true

# Step 2: Remove database volume
echo -e "\n${YELLOW}[2/4] Removing database volume...${NC}"
docker volume rm legalsearch_postgres_data 2>/dev/null || true

# Step 3: Start fresh database
echo -e "\n${GREEN}[3/4] Starting fresh database...${NC}"
docker compose up -d db

echo "Waiting for PostgreSQL to be ready..."
until docker compose exec -T db pg_isready -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" > /dev/null 2>&1; do
    sleep 2
done
echo "Database ready!"

# Step 4: Run fast ingest
echo -e "\n${GREEN}[4/4] Running fast ingest pipeline...${NC}"
docker compose --profile ingest-fast run --rm unified

echo -e "\n${GREEN}=========================================="
echo "  Complete!"
echo "==========================================${NC}"
