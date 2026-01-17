FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for pgvector/psycopg2
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY uv.lock .
RUN curl -LsSf https://astral.sh/uv/0.9.26/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"
RUN uv venv && uv pip install --no-cache-dir .

COPY . .
# Run the app (ML models will download on first run)
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
