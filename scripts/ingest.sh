#!/bin/bash
set -e

echo "=========================================="
echo "  LegalSearch - Data Ingestion Pipeline"
echo "=========================================="

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check Docker
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running.${NC}"
    exit 1
fi

# Check if data directory exists
if [ ! -d ".data" ]; then
    echo -e "${RED}Error: .data directory not found.${NC}"
    echo "Please ensure your raw data is in .data/metadata/raw and .data/GST_judgments"
    exit 1
fi

# Ensure database is running
echo -e "\n${GREEN}[1/5] Ensuring database is running...${NC}"
docker compose up -d db

echo "Waiting for PostgreSQL..."
until docker compose exec -T db pg_isready -U user -d search_db > /dev/null 2>&1; do
    sleep 2
done
echo "Database ready!"

# Build ingest images
echo -e "\n${GREEN}[2/5] Building ingestion images...${NC}"
docker compose --profile ingest build

# Run shard step
echo -e "\n${GREEN}[3/5] Running shard step (extracting PDFs from zips)...${NC}"
docker compose --profile ingest run --rm shard

# Run filter step
echo -e "\n${GREEN}[4/5] Running filter step (ML-based GST relevance filtering)...${NC}"
echo -e "${YELLOW}Note: Running on CPU. This may take a while...${NC}"
docker compose --profile ingest run --rm filter

# Run ETL step
echo -e "\n${GREEN}[5/5] Running ETL step (loading into database)...${NC}"
docker compose --profile ingest run --rm etl

echo -e "\n${GREEN}=========================================="
echo "  Data Ingestion Complete!"
echo "==========================================${NC}"
echo ""
echo "Your data has been processed and loaded into the database."
echo "Start the app with: ./scripts/start.sh"
