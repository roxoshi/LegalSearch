# Base image with all heavy Python dependencies pre-installed
# Build once: docker build -f docker/base-backend.Dockerfile -t legalsearch-base-backend .
# This image rarely needs rebuilding unless dependencies change

FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/0.9.26/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

# Pre-install heavy dependencies that rarely change
# This layer gets cached and reused
RUN uv pip install --system \
    fastapi \
    uvicorn \
    sqlalchemy \
    psycopg2-binary \
    pgvector \
    sentence-transformers \
    pydantic-settings \
    python-dotenv \
    passlib[bcrypt] \
    python-jose[cryptography] \
    python-multipart \
    httpx \
    email-validator

# Pre-download the embedding model (biggest time saver!)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
