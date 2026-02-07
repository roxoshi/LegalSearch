# Start the app
./scripts/start.sh

# Run data ingestion (if you have data in .data/)
./scripts/ingest.sh

# View logs
./scripts/logs.sh
./scripts/logs.sh frontend

# Rebuild a single service
./scripts/rebuild.sh frontend
./scripts/rebuild.sh --no-cache frontend

# Stop everything
./scripts/stop.sh

# Clean up for fresh start
./scripts/cleanup.sh

# Nuclear option (when Docker is broken)
./scripts/nuclear-cleanup.sh

# ONE-TIME: Build base images with pre-installed dependencies
./scripts/build-base-images.sh

# FAST: Rebuild using cached base images
./scripts/rebuild-fast.sh              # all services
./scripts/rebuild-fast.sh backend      # just backend
./scripts/rebuild-fast.sh frontend     # just frontend
