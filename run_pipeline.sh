#!/usr/bin/env bash
set -euo pipefail

# Full ingestion pipeline: shard raw data then ingest into PostgreSQL.
#
# Usage:
#   Local (DB must be running):
#     ./run_pipeline.sh
#
#   Via Docker Compose (starts DB + shard + ETL):
#     docker compose --profile ingest up shard etl

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Step 1: Shard judgments (extract JSON + PDF from raw data) ==="
python -m pipelines.shard_judgments \
    --metadata-dir .data/metadata/raw \
    --judgments-dir .data/GST_judgments \
    --output-dir .data/metadata/processed \
    --pdf-output-dir .data/gst_pdfs

echo ""
echo "=== Step 2: Ingest into PostgreSQL ==="
python etl/ingest.py \
    --json-dir .data/metadata/processed \
    --pdf-dir .data/gst_pdfs

echo ""
echo "=== Pipeline complete ==="
