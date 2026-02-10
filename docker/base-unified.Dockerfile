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

# CRITICAL: Pin huggingface_hub FIRST before any other ML packages
# This version has cached_download which older sentence-transformers needs
RUN uv pip install --system "huggingface_hub>=0.14.0,<0.17.0"

# Install transformers with pinned version (needed by spacy-transformers)
RUN uv pip install --system "transformers>=4.28.0,<4.31.0"

# Install spaCy, spacy-transformers, and spacy-huggingface-hub
RUN uv pip install --system \
    "spacy>=3.7.0,<3.8.0" \
    "spacy-transformers>=1.2.0,<1.4.0" \
    "spacy-huggingface-hub>=0.0.10"

# Download the legal NER model from HuggingFace to /app/models
COPY scripts/install_spacy_model.py /tmp/install_spacy_model.py
RUN python /tmp/install_spacy_model.py && rm /tmp/install_spacy_model.py

# Install sentence-transformers with pinned version
RUN uv pip install --system "sentence-transformers>=2.2.0,<2.3.0"

# Verify spaCy model loads from path
RUN python -c "import spacy; nlp = spacy.load('/app/models/en_legal_ner_trf'); print('Legal NER model OK')"

# Verify sentence-transformers works
RUN python -c "from sentence_transformers import SentenceTransformer; print('SentenceTransformer import OK')"

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

# Install ONNX Runtime for faster embeddings (optional, 2-4x faster on CPU)
RUN uv pip install --system onnx onnxruntime
RUN uv pip install --system "optimum>=1.13.0,<1.15.0"

# Pre-download the sentence-transformers model
RUN python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); print('Embedding model OK')"
