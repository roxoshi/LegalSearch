FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for pgvector/psycopg2
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/0.9.26/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

COPY backend/pyproject.toml backend/uv.lock backend/.env ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .

COPY backend/app /app/app
COPY backend/ingest.py /app/.
COPY pipelines /app/pipelines
COPY etl /app/etl
COPY backend/.data /app/.data

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
