# Backend Dockerfile
# For faster builds, first run: ./scripts/build-base-images.sh
# Then build with: docker compose build --build-arg BASE_IMAGE=legalsearch-base-backend backend

ARG BASE_IMAGE=python:3.13-slim
FROM ${BASE_IMAGE} AS base

WORKDIR /app
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

# === Dependencies stage (cached) ===
FROM base AS deps

# Install system deps and uv (only if not using base image)
RUN which uv || (apt-get update && apt-get install -y \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/0.9.26/install.sh | sh)

# Copy dependency files
COPY backend/pyproject.toml ./

# Install Python dependencies with cache
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    uv pip install --system . 2>/dev/null || pip install .

# === Final stage ===
FROM deps AS final

# Copy application code
COPY backend/app /app/app
COPY pipelines /app/pipelines
COPY etl /app/etl

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
