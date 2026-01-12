FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for pgvector/psycopg2
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN uv pip install --no-cache-dir -r pyproject.toml

COPY . .

# Run the app (ML models will download on first run)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]