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
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi

echo -e "\n${GREEN}[1/4] Building images...${NC}"
docker compose build

echo -e "\n${GREEN}[2/4] Starting database...${NC}"
docker compose up -d db

echo "Waiting for PostgreSQL to be ready..."
until docker compose exec -T db pg_isready -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" > /dev/null 2>&1; do
    sleep 2
    echo "  Waiting..."
done
echo "Database ready!"

echo -e "\n${GREEN}[3/4] Running database migrations...${NC}"
docker compose exec -T db psql -U "${POSTGRES_USER:-user}" -d "${POSTGRES_DB:-search_db}" < scripts/migrations.sql

echo -e "\n${GREEN}[4/4] Starting backend and frontend...${NC}"
docker compose up -d backend frontend

echo -e "\n${GREEN}=========================================="
echo "  LegalSearch is running!"
echo "==========================================${NC}"
echo ""
echo "  Frontend:  http://localhost:${FRONTEND_PORT:-3000}"
echo "  Backend:   http://localhost:${BACKEND_PORT:-8000}"
echo "  Database:  localhost:${DB_PORT:-5432}"
echo ""
echo -e "${YELLOW}View logs:${NC} docker compose logs -f"
echo -e "${YELLOW}Stop:${NC} ./scripts/stop.sh"
