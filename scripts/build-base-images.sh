#!/bin/bash
set -e

echo "=========================================="
echo "  Building Base Images (One-time setup)"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}This builds base images with pre-installed dependencies.${NC}"
echo "These images are cached locally and make subsequent builds MUCH faster."
echo ""

# Build backend base image
echo -e "\n${GREEN}[1/3] Building backend base image...${NC}"
echo "This includes: FastAPI, SQLAlchemy, sentence-transformers, etc."
docker build \
    -f docker/base-backend.Dockerfile \
    -t legalsearch-base-backend \
    .

# Build unified base image (for fast ingest pipeline)
echo -e "\n${GREEN}[2/3] Building unified pipeline base image...${NC}"
echo "This includes: Backend deps + spaCy + legal NER model + embedding model"
echo "This may take a while on first build..."
docker build \
    -f docker/base-unified.Dockerfile \
    -t legalsearch-base-unified \
    .

# Build filter base image (optional, for legacy pipeline)
read -p "Build legacy filter base image? (only needed for old pipeline) [y/N]: " build_filter
if [[ "$build_filter" == "y" || "$build_filter" == "Y" ]]; then
    echo -e "\n${GREEN}[3/3] Building filter base image...${NC}"
    docker build \
        -f docker/base-filter.Dockerfile \
        -t legalsearch-base-filter \
        .
else
    echo -e "\n${YELLOW}Skipping legacy filter base image.${NC}"
fi

echo -e "\n${GREEN}=========================================="
echo "  Base Images Built!"
echo "==========================================${NC}"
echo ""
echo "Fast rebuild commands:"
echo ""
echo "  # Unified pipeline (recommended):"
echo "  docker compose --profile ingest-fast build unified"
echo ""
echo "  # Backend:"
echo "  docker compose build backend"
echo ""
echo "Or use scripts:"
echo "  ./scripts/ingest-fast.sh"
echo "  ./scripts/reset-db-and-ingest.sh"
