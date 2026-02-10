# LegalSearch Deployment Guide

This guide covers deploying LegalSearch across three environments:

| Environment | Purpose | Where it runs | What it does |
|---|---|---|---|
| **Staging** | Build the database | Local machine (GPU) | Runs the full pipeline (extract, filter, embed, ingest) |
| **Dev** | Development / testing | VPS or localhost | Runs db + backend + frontend, receives DB sync from staging |
| **Prod** | Production | Cloud VPS | Same as dev, with resource limits and hardening |

The core idea: the database is built once on staging (which has GPU for the ML pipeline), then synced to dev/prod. Dev and prod never run the pipeline themselves.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Repository Structure](#repository-structure)
3. [Environment Setup](#environment-setup)
4. [Deploying to Dev](#deploying-to-dev)
5. [Deploying to Prod](#deploying-to-prod)
6. [Building the Database on Staging](#building-the-database-on-staging)
7. [Database Sync: Staging to Dev/Prod](#database-sync-staging-to-devprod)
8. [Reverse Proxy and SSL (Prod)](#reverse-proxy-and-ssl-prod)
9. [Maintenance and Operations](#maintenance-and-operations)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### All environments

- Docker Engine 24+ and Docker Compose v2 (comes with Docker Engine)
- Git
- 2GB+ free RAM (4GB+ recommended for prod)

Verify Docker is working:

```bash
docker info
docker compose version   # needs v2.20+
```

### Staging only (for building the database)

- NVIDIA GPU with CUDA 12.1+ drivers (for ML filtering; falls back to CPU if unavailable)
- 16GB+ RAM recommended (the pipeline processes hundreds of thousands of PDFs)
- Raw data in `.data/` directory:
  - `.data/metadata/raw/` — Parquet files (SC-GST-*.parquet, HC-GST-*/)
  - `.data/GST_judgments/` — Zip/tar archives of PDF judgments

### Dev/Prod VPS

- A VPS with at least 2 vCPUs, 4GB RAM, 40GB disk
- SSH access from staging (for database sync)
- A domain name pointed to the VPS IP (for prod with SSL)

---

## Repository Structure

```
docker-compose.yml              # Base: db + backend + frontend (all environments)
docker-compose.staging.yml      # Override: adds pipeline services (staging only)
docker-compose.prod.yml         # Override: resource limits, log rotation, hardening

envs/
  .env.staging.example          # Template for staging
  .env.dev.example              # Template for dev
  .env.prod.example             # Template for prod
  .gitignore                    # Prevents committing real env files

scripts/
  deploy.sh                     # Unified entry point for all environments
  start.sh                      # Legacy start script (backward compatible)
  db-dump.sh                    # Create compressed database dump
  db-restore.sh                 # Restore database from dump
  db-sync.sh                    # Orchestrate sync (full or incremental)
  db-incremental-export.py      # Export changed rows as JSONL
  db-incremental-import.py      # Import JSONL into target database
  migrations.sql                # Idempotent database migrations
  build-base-images.sh          # One-time: build Docker base images with deps
```

---

## Environment Setup

Every environment needs an env file. Start by copying the example template:

```bash
# For dev:
cp envs/.env.dev.example envs/.env.dev

# For prod:
cp envs/.env.prod.example envs/.env.prod

# For staging:
cp envs/.env.staging.example envs/.env.staging
```

Then edit the file and fill in all `<placeholder>` values.

### Generating secrets

```bash
# Generate a strong random password (for POSTGRES_PASSWORD)
openssl rand -base64 24

# Generate SECRET_KEY (for JWT signing)
openssl rand -hex 32
```

### Required variables

| Variable | Description | Example |
|---|---|---|
| `POSTGRES_USER` | PostgreSQL username | `legalsearch` |
| `POSTGRES_PASSWORD` | PostgreSQL password | (generate with openssl) |
| `POSTGRES_DB` | Database name | `search_db` |
| `SECRET_KEY` | JWT signing key | (generate with openssl) |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | `494421...` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth secret | `GOCSPX-...` |

### Optional variables

| Variable | Default | Description |
|---|---|---|
| `DB_PORT` | `5432` | Host port for PostgreSQL |
| `BACKEND_PORT` | `8000` | Host port for FastAPI |
| `FRONTEND_PORT` | `3000` | Host port for Next.js |
| `BACKEND_BASE_IMAGE` | `python:3.13-slim` | Base Docker image for backend |
| `MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `STAGING_HOST` | — | Staging server IP (for sync from dev/prod) |
| `STAGING_SSH_USER` | — | SSH user on staging (for sync) |

---

## Deploying to Dev

This is the simplest deployment: database + backend + frontend. The database will be populated via sync from staging.

### Step 1: Clone the repository

```bash
ssh your-dev-server
git clone https://github.com/your-org/LegalSearch.git
cd LegalSearch
```

### Step 2: Create the environment file

```bash
cp envs/.env.dev.example envs/.env.dev
```

Edit `envs/.env.dev` and set your values:

```bash
ENVIRONMENT=dev
POSTGRES_USER=legalsearch
POSTGRES_PASSWORD=your-strong-password-here
POSTGRES_DB=search_db
SECRET_KEY=your-64-char-hex-key-here
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
BACKEND_BASE_IMAGE=python:3.13-slim
MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
NEXT_PUBLIC_API_PORT=8000
DB_PORT=5432
BACKEND_PORT=8000
FRONTEND_PORT=3000
STAGING_HOST=192.168.1.100
STAGING_SSH_USER=rj
```

### Step 3: Start the services

```bash
./scripts/deploy.sh dev start
```

This will:
1. Build Docker images for backend and frontend
2. Start the PostgreSQL database with pgvector
3. Wait for the database to be healthy
4. Run idempotent migrations (creates tables, indices, triggers)
5. Start backend (FastAPI on port 8000) and frontend (Next.js on port 3000)

### Step 4: Verify

```bash
# Check service status
./scripts/deploy.sh dev status

# Check the frontend is reachable
curl -s http://localhost:3000 | head -5

# Check the backend API
curl -s http://localhost:8000/docs | head -5
```

At this point the database is empty. Populate it by syncing from staging (see [Database Sync](#database-sync-staging-to-devprod) below).

### Dev: Useful commands

```bash
./scripts/deploy.sh dev start      # Start all services
./scripts/deploy.sh dev stop       # Stop all services
./scripts/deploy.sh dev rebuild    # Rebuild images from scratch and restart
./scripts/deploy.sh dev logs       # Tail all service logs
./scripts/deploy.sh dev status     # Show service health and document count
```

---

## Deploying to Prod

Production deployment is identical to dev with an additional compose override that adds:
- Resource limits (memory/CPU caps on all services)
- Log rotation (50MB max per file, 5 files max)
- Database port bound to `127.0.0.1` only (not exposed to the internet)
- `restart: unless-stopped` policy on all services

### Step 1: Clone and configure

```bash
ssh your-prod-server
git clone https://github.com/your-org/LegalSearch.git
cd LegalSearch

cp envs/.env.prod.example envs/.env.prod
```

Edit `envs/.env.prod` with **strong, unique** credentials:

```bash
ENVIRONMENT=prod
POSTGRES_USER=legalsearch
POSTGRES_PASSWORD=<very-strong-password>
POSTGRES_DB=search_db
SECRET_KEY=<openssl-rand-hex-32>
GOOGLE_CLIENT_ID=<your-production-google-client-id>
GOOGLE_CLIENT_SECRET=<your-production-google-secret>
BACKEND_BASE_IMAGE=python:3.13-slim
MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
NEXT_PUBLIC_API_PORT=8000
DB_PORT=5432
BACKEND_PORT=8000
FRONTEND_PORT=3000
STAGING_HOST=<staging-machine-ip>
STAGING_SSH_USER=<ssh-user>
```

### Step 2: Start production services

```bash
./scripts/deploy.sh prod start
```

The deploy script detects the `prod` environment and automatically includes `docker-compose.prod.yml`, which layers on the production hardening.

### Step 3: Verify

```bash
./scripts/deploy.sh prod status
```

Then confirm the database port is only accessible locally:

```bash
# This should fail from outside the server:
# nc -zv <server-public-ip> 5432

# This should work from the server itself:
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db pg_isready -U legalsearch -d search_db
```

### Step 4: Populate the database

Sync from staging (see [Database Sync](#database-sync-staging-to-devprod) below).

### What the prod override changes

The `docker-compose.prod.yml` override applies these settings on top of the base:

| Service | Memory Limit | CPU Limit | Extra |
|---|---|---|---|
| `db` | 2 GB | 2.0 | Port bound to 127.0.0.1 |
| `backend` | 2 GB | 2.0 | — |
| `frontend` | 512 MB | 1.0 | — |

All services get `json-file` log driver with 50MB max size and 5 file rotation.

You can adjust these limits in `docker-compose.prod.yml` based on your VPS specs.

---

## Building the Database on Staging

Staging is where the raw PDF data is processed through the ML pipeline to build the search database. This is the only environment that runs the pipeline services.

### Step 1: Configure staging

```bash
cp envs/.env.staging.example envs/.env.staging
# Edit with your values
```

### Step 2: Build base images (one-time)

The pipeline uses large Docker images with pre-installed ML models (spaCy NER, sentence-transformers). Build them once:

```bash
./scripts/build-base-images.sh
```

This creates:
- `legalsearch-base-backend` — FastAPI + SQLAlchemy + sentence-transformers
- `legalsearch-base-unified` — All of the above + spaCy + legal NER model + ONNX Runtime

These images are cached locally and make subsequent builds fast.

### Step 3: Place raw data

Ensure your raw data is in the expected locations:

```
.data/
  metadata/raw/
    SC-GST-2018.parquet       # Supreme Court metadata
    SC-GST-2019.parquet
    ...
    HC-GST-2018/              # High Court metadata (hierarchical)
      court=2_5/
        bench=Chandigarh/
          data.tar
  GST_judgments/
    SC-GST-2018.zip           # Supreme Court PDFs
    SC-GST-2019.zip
    ...
    HC-GST-2018/              # High Court PDFs (in tar archives)
      court=2_5/
        bench=Chandigarh/
          data.tar
```

### Step 4: Start the database and run the pipeline

```bash
# Start the core services first
./scripts/deploy.sh staging start

# Run the unified pipeline (recommended)
# This uses docker-compose.staging.yml which has the pipeline services
docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  --profile ingest-fast up unified

# Or run HC only:
docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  --profile ingest-hc up unified-hc
```

The pipeline processes data in 6 steps:
1. **Extract** — Read PDFs from zip/tar archives
2. **NER Filter** — Use spaCy legal NER to identify GST-relevant cases
3. **Keyword Filter** — Fallback keyword matching for edge cases
4. **HTML Convert** — Convert PDFs to HTML for display
5. **Embed** — Generate 384-dim vector embeddings using sentence-transformers
6. **Ingest** — Bulk insert documents and chunks into PostgreSQL

For a full SC+HC dataset, this typically takes several hours depending on your hardware.

### Step 5: Verify the database

```bash
./scripts/deploy.sh staging status
```

You should see row counts for both `documents` and `document_chunks` tables.

---

## Database Sync: Staging to Dev/Prod

Once the database is built on staging, sync it to your dev/prod servers. Two sync modes are available:

### Full Sync (recommended for first deployment)

Creates a compressed `pg_dump`, transfers it via SCP, and restores on the target. This **replaces** all data on the target.

#### Option A: Automated (requires SSH access from staging to target)

```bash
# From the staging machine:
./scripts/db-sync.sh full your-prod-server.com
```

This will:
1. Run `pg_dump` on staging (compressed, ~100-500MB depending on dataset size)
2. SCP the dump to the target server
3. SSH into the target and run `pg_restore`
4. Run migrations on the target
5. Record the sync timestamp for future incremental syncs

#### Option B: Manual (no direct SSH needed)

**On staging** — create the dump:

```bash
./scripts/db-dump.sh
# Output: .data/db-dumps/legalsearch_20240615_143022.dump
```

**Transfer the dump** to your target server by any method:

```bash
scp .data/db-dumps/legalsearch_20240615_143022.dump user@prod-server:~/LegalSearch/.data/db-dumps/
```

**On the target server** — restore:

```bash
cd ~/LegalSearch

# Make sure the database is running
./scripts/deploy.sh prod start

# Restore (will prompt for confirmation)
./scripts/db-restore.sh .data/db-dumps/legalsearch_20240615_143022.dump
```

The restore script will:
1. Drop existing tables (`document_chunks`, `documents`, `users`)
2. Run `pg_restore` from the dump
3. Run migrations (ensures `updated_at` trigger and indices exist)
4. Print row counts to confirm

### Incremental Sync (for subsequent updates)

After the initial full sync, use incremental sync to transfer only new/changed documents. This uses the `updated_at` timestamp column to detect changes.

```bash
# From staging:
./scripts/db-sync.sh incremental your-prod-server.com
```

This will:
1. Query `documents WHERE updated_at > last_sync_timestamp`
2. Export matching documents (with their chunks and embeddings) to a JSONL file
3. SCP the JSONL to the target
4. On the target: for each document, delete existing by `case_id` and reinsert
5. Update the sync timestamp

The last sync timestamp is stored in `.data/db-dumps/.last_sync_timestamp`. If this file doesn't exist, the script falls back to a full sync.

#### Manual incremental sync

**On staging:**

```bash
python scripts/db-incremental-export.py \
  --since "2024-06-01T00:00:00+00:00" \
  --output .data/db-dumps/incremental.jsonl
```

**Transfer:**

```bash
scp .data/db-dumps/incremental.jsonl user@prod-server:~/LegalSearch/.data/db-dumps/
```

**On target:**

```bash
DATABASE_URL=postgresql://legalsearch:password@localhost:5432/search_db \
  python scripts/db-incremental-import.py \
  --input .data/db-dumps/incremental.jsonl
```

---

## Reverse Proxy and SSL (Prod)

For production, you should put the application behind a reverse proxy with SSL termination. Here's a setup using Nginx and Let's Encrypt.

### Install Nginx and Certbot

```bash
sudo apt update
sudo apt install nginx certbot python3-certbot-nginx
```

### Create Nginx config

```bash
sudo tee /etc/nginx/sites-available/legalsearch <<'EOF'
server {
    listen 80;
    server_name yourdomain.com;

    # Frontend
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Backend API
    location /search {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /document {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /auth {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /docs {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /openapi.json {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/legalsearch /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### Add SSL with Let's Encrypt

```bash
sudo certbot --nginx -d yourdomain.com
```

Certbot will automatically modify the Nginx config to add SSL and set up auto-renewal.

### Update the frontend API port

When behind a reverse proxy, the frontend needs to use port 443 (HTTPS) instead of 8000 to reach the backend API. Update your env file:

```bash
# In envs/.env.prod
NEXT_PUBLIC_API_PORT=443
```

Then rebuild the frontend (the API port is baked in at build time):

```bash
./scripts/deploy.sh prod rebuild
```

### Firewall

Lock down the VPS to only allow HTTP, HTTPS, and SSH:

```bash
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # HTTP (redirect to HTTPS)
sudo ufw allow 443/tcp    # HTTPS
sudo ufw enable
```

Ports 3000, 5432, and 8000 should NOT be exposed to the internet. They're only accessed via localhost by the Nginx reverse proxy.

---

## Maintenance and Operations

### Viewing logs

```bash
# All services
./scripts/deploy.sh prod logs

# Specific service
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f db
```

### Updating the application

```bash
cd ~/LegalSearch
git pull origin main
./scripts/deploy.sh prod rebuild
```

This rebuilds all images from scratch (including `--no-cache`) and restarts the services. The database volume is preserved, so no data is lost.

For a quicker update that uses Docker layer caching:

```bash
git pull origin main
./scripts/deploy.sh prod stop
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
./scripts/deploy.sh prod start
```

### Database backups

Set up a cron job on your prod server to create periodic backups:

```bash
# Edit crontab
crontab -e

# Add a daily backup at 2 AM
0 2 * * * cd /home/user/LegalSearch && ENV_FILE=envs/.env.prod ./scripts/db-dump.sh >> /var/log/legalsearch-backup.log 2>&1
```

The dump script automatically keeps only the last 5 dumps.

### Checking database health

```bash
# Document and chunk counts
./scripts/deploy.sh prod status

# Connect to the database directly
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db \
  psql -U legalsearch -d search_db

# Inside psql:
\dt                                    -- list tables
SELECT COUNT(*) FROM documents;        -- document count
SELECT COUNT(*) FROM document_chunks;  -- chunk count
SELECT MIN(updated_at), MAX(updated_at) FROM documents;  -- data freshness
```

### Scaling considerations

The default resource limits in `docker-compose.prod.yml` are conservative. Adjust based on your VPS:

| VPS Size | db memory | backend memory | frontend memory |
|---|---|---|---|
| 4 GB RAM | 2 GB | 1.5 GB | 512 MB |
| 8 GB RAM | 4 GB | 3 GB | 512 MB |
| 16 GB RAM | 8 GB | 6 GB | 1 GB |

The backend memory matters most because it loads the sentence-transformers model into memory at startup (~400MB) for query-time embedding.

---

## Troubleshooting

### "Set POSTGRES_USER in your env file" error on `docker compose up`

You're running docker compose without loading the env file. Use the deploy script:

```bash
./scripts/deploy.sh dev start
```

Or load the env file manually:

```bash
set -a; source envs/.env.dev; set +a
docker compose up -d
```

### Backend can't connect to database

Check that the database is healthy:

```bash
docker compose ps
docker compose logs db
```

The backend uses `depends_on: db: condition: service_healthy`, so it won't start until the db healthcheck passes. If the healthcheck is failing, check that `POSTGRES_USER` and `POSTGRES_DB` match between your env file and what's actually in the database volume.

If you changed credentials after the database was first created, the PostgreSQL volume still has the old credentials. Either:

```bash
# Option 1: Reset the volume (loses data!)
docker compose down -v
./scripts/deploy.sh dev start

# Option 2: Change the password inside the database
docker compose exec db psql -U old_username -d search_db -c "ALTER USER old_username WITH PASSWORD 'new_password';"
```

### Frontend shows empty search results

The database is likely empty. Check:

```bash
./scripts/deploy.sh dev status
```

If document count is 0, you need to sync from staging. See [Database Sync](#database-sync-staging-to-devprod).

### Pipeline runs out of memory on staging

The HC dataset is large (1M+ records). The pipeline writes PDFs to a staging directory on disk rather than holding them in memory. If you still hit OOM:

- Limit to specific years: `--years 2018 2019`
- Reduce batch size: `--batch-size 25`
- Run SC and HC separately:
  ```bash
  # SC first
  docker compose -f docker-compose.yml -f docker-compose.staging.yml \
    --profile ingest-fast run --rm unified \
    python -m pipelines.unified_pipeline --court-type sc --metadata-dir /app/.data/metadata/raw --judgments-dir /app/.data/GST_judgments

  # Then HC
  docker compose -f docker-compose.yml -f docker-compose.staging.yml \
    --profile ingest-hc up unified-hc
  ```

### `pg_restore` errors during sync

`pg_restore` commonly outputs warnings (e.g., "role does not exist") that are harmless. The restore script uses `|| true` to suppress the non-zero exit code. Check the actual row counts after restore to verify:

```bash
./scripts/deploy.sh prod status
```

### Port conflicts

If ports 3000, 5432, or 8000 are already in use, change them in your env file:

```bash
# In envs/.env.dev
DB_PORT=5433
BACKEND_PORT=8001
FRONTEND_PORT=3001
NEXT_PUBLIC_API_PORT=8001
```

Note: `NEXT_PUBLIC_API_PORT` must match `BACKEND_PORT` because it's the port the browser uses to reach the API. After changing it, rebuild the frontend (this value is baked in at build time):

```bash
./scripts/deploy.sh dev rebuild
```
