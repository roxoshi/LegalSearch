#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

usage() {
    echo "Usage: $0 <environment> <command>"
    echo ""
    echo "Environments: staging, dev, prod"
    echo "Commands:     start, stop, rebuild, logs, status"
    echo ""
    echo "Examples:"
    echo "  $0 staging start        # Start staging (db + backend + frontend)"
    echo "  $0 staging rebuild      # Rebuild and restart all services"
    echo "  $0 prod start           # Start production with hardening"
    echo "  $0 dev logs             # Tail logs for dev"
    echo "  $0 staging status       # Show service status and document count"
    exit 1
}

if [ $# -lt 2 ]; then
    usage
fi

ENVIRONMENT="$1"
COMMAND="$2"

cd "$(dirname "$0")/.."

# Validate environment
ENV_FILE="envs/.env.${ENVIRONMENT}"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: $ENV_FILE not found.${NC}"
    echo "Copy the example file and fill in values:"
    echo "  cp envs/.env.${ENVIRONMENT}.example $ENV_FILE"
    exit 1
fi

# Load environment
set -a; source "$ENV_FILE"; set +a

# Build compose command with correct override files
COMPOSE_CMD="docker compose -f docker-compose.yml"

case "$ENVIRONMENT" in
    staging)
        COMPOSE_CMD="$COMPOSE_CMD -f docker-compose.staging.yml"
        ;;
    prod)
        COMPOSE_CMD="$COMPOSE_CMD -f docker-compose.prod.yml"
        ;;
    dev)
        # Dev uses base docker-compose.yml only
        ;;
    *)
        echo -e "${RED}Error: Unknown environment '$ENVIRONMENT'${NC}"
        usage
        ;;
esac

# Export for docker compose variable substitution
export COMPOSE_PROJECT_NAME="legalsearch-${ENVIRONMENT}"

run_migrations() {
    echo -e "${GREEN}Running database migrations...${NC}"
    $COMPOSE_CMD exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB:-search_db}" < scripts/migrations.sql
}

wait_for_db() {
    echo "Waiting for PostgreSQL to be ready..."
    until $COMPOSE_CMD exec -T db pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB:-search_db}" > /dev/null 2>&1; do
        sleep 2
        echo "  Waiting..."
    done
    echo "Database ready!"
}

case "$COMMAND" in
    start)
        echo -e "${GREEN}Starting LegalSearch ($ENVIRONMENT)...${NC}"
        $COMPOSE_CMD build
        $COMPOSE_CMD up -d db
        wait_for_db
        run_migrations
        $COMPOSE_CMD up -d backend frontend
        echo ""
        echo -e "${GREEN}LegalSearch ($ENVIRONMENT) is running!${NC}"
        echo "  Frontend:  http://localhost:${FRONTEND_PORT:-3000}"
        echo "  Backend:   http://localhost:${BACKEND_PORT:-8000}"
        echo "  Database:  localhost:${DB_PORT:-5432}"
        ;;

    stop)
        echo -e "${YELLOW}Stopping LegalSearch ($ENVIRONMENT)...${NC}"
        $COMPOSE_CMD down
        echo "Stopped."
        ;;

    rebuild)
        echo -e "${YELLOW}Rebuilding LegalSearch ($ENVIRONMENT)...${NC}"
        $COMPOSE_CMD down
        $COMPOSE_CMD build --no-cache
        $COMPOSE_CMD up -d db
        wait_for_db
        run_migrations
        $COMPOSE_CMD up -d backend frontend
        echo -e "${GREEN}Rebuild complete!${NC}"
        ;;

    logs)
        $COMPOSE_CMD logs -f
        ;;

    status)
        echo -e "${CYAN}=== LegalSearch ($ENVIRONMENT) Status ===${NC}"
        echo ""
        echo -e "${CYAN}Services:${NC}"
        $COMPOSE_CMD ps
        echo ""
        # Show document count if db is running
        if $COMPOSE_CMD exec -T db pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB:-search_db}" > /dev/null 2>&1; then
            echo -e "${CYAN}Database:${NC}"
            $COMPOSE_CMD exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB:-search_db}" -c \
                "SELECT 'documents' AS table_name, COUNT(*) FROM documents UNION ALL SELECT 'document_chunks', COUNT(*) FROM document_chunks;" 2>/dev/null || echo "  Tables not yet created."
        fi
        ;;

    *)
        echo -e "${RED}Error: Unknown command '$COMMAND'${NC}"
        usage
        ;;
esac
