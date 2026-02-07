# Base image for unified pipeline with all dependencies pre-installed
# Build once: docker build -f docker/base-unified.Dockerfile -t legalsearch-base-unified .

# Use PyTorch base image - has compatible numpy/torch versions
FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

WORKDIR /app
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/0.9.26/install.sh | sh

# Install spaCy first with compatible versions
RUN pip install --no-cache-dir "spacy>=3.7.0,<3.8.0"

# Install the legal NER model
RUN pip install --no-cache-dir https://huggingface.co/opennyaiorg/en_legal_ner_trf/resolve/main/en_legal_ner_trf-any-py3-none-any.whl

# Verify spaCy model loads
RUN python -c "import spacy; nlp = spacy.load('en_legal_ner_trf'); print('Legal NER model OK')"

# Pin compatible versions of transformers and sentence-transformers
# Use older transformers to match the spaCy legal NER model
RUN pip install --no-cache-dir \
    "transformers>=4.25.0,<4.30.0" \
    "sentence-transformers>=2.2.0,<2.3.0"

# Install remaining dependencies (pin pydantic v1 for compatibility)
RUN uv pip install --system \
    fastapi \
    uvicorn \
    sqlalchemy \
    psycopg2-binary \
    pgvector \
    "pydantic>=1.10.0,<2.0.0" \
    python-jose \
    passlib \
    bcrypt \
    httpx \
    python-multipart \
    beautifulsoup4 \
    pymupdf \
    pdfplumber \
    pandas \
    pyarrow \
    tqdm

# Pre-download the sentence-transformers model
RUN python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); print('Embedding model OK')"
