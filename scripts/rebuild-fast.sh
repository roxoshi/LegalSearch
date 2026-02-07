#!/bin/bash
set -e

echo "=========================================="
echo "  Fast Rebuild (using base images)"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check if base images exist
BACKEND_BASE="python:3.13-slim"
FILTER_BASE="pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime"

if docker image inspect legalsearch-base-backend >/dev/null 2>&1; then
    BACKEND_BASE="legalsearch-base-backend"
    echo -e "${GREEN}Using cached backend base image${NC}"
else
    echo -e "${YELLOW}Backend base image not found. Run ./scripts/build-base-images.sh for faster builds.${NC}"
fi

if docker image inspect legalsearch-base-filter >/dev/null 2>&1; then
    FILTER_BASE="legalsearch-base-filter"
    echo -e "${GREEN}Using cached filter base image${NC}"
else
    echo -e "${YELLOW}Filter base image not found (optional).${NC}"
fi

# Parse arguments
SERVICE="${1:-all}"

case $SERVICE in
    backend)
        echo -e "\n${GREEN}Rebuilding backend...${NC}"
        docker compose build --build-arg BASE_IMAGE=$BACKEND_BASE backend
        docker compose up -d backend
        ;;
    frontend)
        echo -e "\n${GREEN}Rebuilding frontend...${NC}"
        docker compose build frontend
        docker compose up -d frontend
        ;;
    filter)
        echo -e "\n${GREEN}Rebuilding filter...${NC}"
        docker compose build --build-arg BASE_IMAGE=$FILTER_BASE filter
        ;;
    all)
        echo -e "\n${GREEN}Rebuilding all services...${NC}"
        docker compose build \
            --build-arg BASE_IMAGE=$BACKEND_BASE
        docker compose up -d
        ;;
    *)
        echo "Usage: $0 [backend|frontend|filter|all]"
        exit 1
        ;;
esac

echo -e "\n${GREEN}Done!${NC}"
