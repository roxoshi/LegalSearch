#!/bin/bash

echo "=========================================="
echo "  LegalSearch - Cleanup"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}This will remove all containers, images, volumes, and networks for LegalSearch.${NC}"
echo ""
read -p "Are you sure? (y/N): " confirm

if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Cancelled."
    exit 0
fi

echo -e "\n${GREEN}[1/5] Stopping containers...${NC}"
docker compose down --remove-orphans 2>/dev/null || true

echo -e "\n${GREEN}[2/5] Removing volumes...${NC}"
docker compose down -v 2>/dev/null || true

echo -e "\n${GREEN}[3/5] Removing project images...${NC}"
docker images | grep legalsearch | awk '{print $3}' | xargs -r docker rmi -f 2>/dev/null || true

echo -e "\n${GREEN}[4/5] Cleaning up networks...${NC}"
docker network ls | grep legalsearch | awk '{print $1}' | xargs -r docker network rm 2>/dev/null || true

echo -e "\n${GREEN}[5/5] Pruning unused resources...${NC}"
docker network prune -f
docker volume prune -f

echo -e "\n${GREEN}=========================================="
echo "  Cleanup Complete!"
echo "==========================================${NC}"
echo ""
echo "To rebuild from scratch: ./scripts/start.sh"
