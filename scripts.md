# Scripts Reference

## App Lifecycle

```bash
# Start the app (db, backend, frontend)
./scripts/start.sh

# View logs
./scripts/logs.sh
./scripts/logs.sh frontend

# Stop everything
./scripts/stop.sh

# Clean up for fresh start
./scripts/cleanup.sh

# Nuclear option (when Docker is broken)
./scripts/nuclear-cleanup.sh
```

## Data Ingestion

### Unified Pipeline (Recommended)

The unified pipeline combines extraction, filtering, and ingestion in one optimized step.
Supports Supreme Court (SC), High Court (HC), or both.

```bash
# Ingest all courts (SC + HC)
./scripts/ingest-fast.sh

# Ingest with options
./scripts/ingest-fast.sh --limit 50           # limit to 50 records
./scripts/ingest-fast.sh --skip-filter         # skip NER filtering

# Reset database and re-ingest
./scripts/reset-db-and-ingest.sh
```

#### Running the pipeline directly (without Docker)

```bash
# All courts (default)
uv run python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/raw \
  --judgments-dir .data/GST_judgments \
  --court-type all

# Supreme Court only
uv run python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/raw \
  --judgments-dir .data/GST_judgments \
  --court-type sc

# High Court only
uv run python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/raw \
  --judgments-dir .data/GST_judgments \
  --court-type hc

# High Court, specific years, limited records, skip NER filter
uv run python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/raw \
  --judgments-dir .data/GST_judgments \
  --court-type hc --years 2018 2019 --limit 50 --skip-filter

# Use ONNX Runtime for faster embeddings on CPU
uv run python -m pipelines.unified_pipeline \
  --court-type all --use-onnx
```

#### Running via Docker Compose

```bash
# All courts
docker compose --profile ingest-fast up unified

# High Court only
docker compose --profile ingest-hc up unified-hc
```

### Legacy Multi-Step Pipeline (SC only)

Runs shard, filter, and ETL as separate steps. Only processes Supreme Court data.

```bash
./scripts/ingest.sh
```

## Building & Rebuilding

```bash
# ONE-TIME: Build base images with pre-installed dependencies
./scripts/build-base-images.sh

# Rebuild a single service
./scripts/rebuild.sh frontend
./scripts/rebuild.sh --no-cache frontend

# FAST: Rebuild using cached base images
./scripts/rebuild-fast.sh              # all services
./scripts/rebuild-fast.sh backend      # just backend
./scripts/rebuild-fast.sh frontend     # just frontend
```

## Linting & Testing

```bash
# Run linting, type checking, and tests
./scripts/lint.sh

# Auto-fix lint issues
./scripts/lint.sh --fix

# Run tests directly
uv run pytest backend/tests -v          # backend tests
uv run pytest pipelines/tests -v        # pipeline tests
uv run pytest etl/tests -v              # ETL tests
```
