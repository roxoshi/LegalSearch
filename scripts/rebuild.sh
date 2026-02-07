#!/bin/bash
set -e

echo "=========================================="
echo "  LegalSearch - Rebuild & Restart"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Parse arguments
NO_CACHE=""
SERVICE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        frontend|backend|db)
            SERVICE="$1"
            shift
            ;;
        *)
            echo "Usage: $0 [--no-cache] [frontend|backend|db]"
            exit 1
            ;;
    esac
done

if [ -n "$SERVICE" ]; then
    echo -e "${GREEN}Rebuilding $SERVICE...${NC}"
    docker compose build $NO_CACHE $SERVICE
    docker compose up -d $SERVICE
    echo -e "${GREEN}$SERVICE rebuilt and restarted.${NC}"
else
    echo -e "${GREEN}Rebuilding all services...${NC}"
    docker compose down
    docker compose build $NO_CACHE

    # Start services
    ./scripts/start.sh
fi

echo ""
echo -e "${YELLOW}View logs:${NC} docker compose logs -f"
