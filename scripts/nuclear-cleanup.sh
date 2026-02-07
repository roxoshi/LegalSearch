#!/bin/bash

echo "=========================================="
echo "  LegalSearch - Nuclear Cleanup"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${RED}WARNING: This will perform an aggressive Docker cleanup!${NC}"
echo ""
echo "This script will:"
echo "  - Stop ALL running containers (not just LegalSearch)"
echo "  - Remove ALL stopped containers"
echo "  - Remove ALL unused networks"
echo "  - Remove LegalSearch volumes and images"
echo "  - Restart Docker daemon"
echo ""
read -p "Are you absolutely sure? (type 'yes' to confirm): " confirm

if [[ "$confirm" != "yes" ]]; then
    echo "Cancelled."
    exit 0
fi

echo -e "\n${YELLOW}[1/7] Stopping all containers...${NC}"
docker stop $(docker ps -aq) 2>/dev/null || true

echo -e "\n${YELLOW}[2/7] Removing all containers...${NC}"
docker rm $(docker ps -aq) 2>/dev/null || true

echo -e "\n${YELLOW}[3/7] Removing all networks...${NC}"
docker network rm $(docker network ls -q) 2>/dev/null || true

echo -e "\n${YELLOW}[4/7] Removing LegalSearch volumes...${NC}"
docker volume ls | grep legalsearch | awk '{print $2}' | xargs -r docker volume rm 2>/dev/null || true

echo -e "\n${YELLOW}[5/7] Removing LegalSearch images...${NC}"
docker images | grep -E "legalsearch|pgvector" | awk '{print $3}' | xargs -r docker rmi -f 2>/dev/null || true

echo -e "\n${YELLOW}[6/7] Pruning everything...${NC}"
docker system prune -f
docker volume prune -f
docker network prune -f

echo -e "\n${YELLOW}[7/7] Restarting Docker daemon...${NC}"
if command -v systemctl &> /dev/null; then
    sudo systemctl restart docker
    echo "Docker daemon restarted."
else
    echo "Please restart Docker Desktop manually."
fi

echo -e "\n${GREEN}=========================================="
echo "  Nuclear Cleanup Complete!"
echo "==========================================${NC}"
echo ""
echo "Docker has been reset. To rebuild LegalSearch:"
echo "  ./scripts/start.sh"
