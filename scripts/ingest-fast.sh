#!/bin/bash
set -e

echo "=========================================="
echo "  LegalSearch - Fast Unified Pipeline"
echo "=========================================="
echo ""
echo "This optimized pipeline combines all steps:"
echo "  - Direct streaming from zip files"
echo "  - Batch embedding generation (512+ chunks at once)"
echo "  - Bulk database inserts"
echo "  - Parallel PDF processing"
echo ""

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse arguments
BATCH_SIZE=${BATCH_SIZE:-50}
LIMIT=${LIMIT:-0}
SKIP_FILTER=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --batch-size) BATCH_SIZE="$2"; shift ;;
        --limit) LIMIT="$2"; shift ;;
        --skip-filter) SKIP_FILTER="--skip-filter" ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

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
echo -e "\n${GREEN}[1/2] Ensuring database is running...${NC}"
docker compose up -d db

echo "Waiting for PostgreSQL..."
until docker compose exec -T db pg_isready -U user -d search_db > /dev/null 2>&1; do
    sleep 2
done
echo "Database ready!"

# Build unified image
echo -e "\n${GREEN}[2/2] Running unified pipeline...${NC}"
echo -e "${CYAN}Configuration:${NC}"
echo "  Batch size: $BATCH_SIZE"
if [ "$LIMIT" -gt 0 ]; then
    echo "  Limit: $LIMIT documents"
fi
if [ -n "$SKIP_FILTER" ]; then
    echo "  NER Filter: SKIPPED"
fi
echo ""

# Build and run
docker compose --profile ingest-fast build unified

# Construct command with optional args
CMD="python -m pipelines.unified_pipeline --metadata-dir /app/.data/metadata/raw --judgments-dir /app/.data/GST_judgments --batch-size $BATCH_SIZE"
if [ "$LIMIT" -gt 0 ]; then
    CMD="$CMD --limit $LIMIT"
fi
if [ -n "$SKIP_FILTER" ]; then
    CMD="$CMD --skip-filter"
fi

docker compose --profile ingest-fast run --rm unified $CMD

echo -e "\n${GREEN}=========================================="
echo "  Unified Pipeline Complete!"
echo "==========================================${NC}"
echo ""
echo "Your data has been processed and loaded into the database."
echo "Start the app with: ./scripts/start.sh"
