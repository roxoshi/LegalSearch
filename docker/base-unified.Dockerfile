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

# Install transformers (needed by spacy-transformers and sentence-transformers)
RUN uv pip install --system "transformers>=4.34.0,<4.41.0"

# Install spaCy, spacy-transformers, and spacy-huggingface-hub
RUN uv pip install --system \
    "spacy>=3.7.0,<3.8.0" \
    "spacy-transformers>=1.2.0,<1.4.0" \
    "spacy-huggingface-hub>=0.0.10"

# Download the legal NER model from HuggingFace to /app/models
COPY scripts/install_spacy_model.py /tmp/install_spacy_model.py
RUN python /tmp/install_spacy_model.py && rm /tmp/install_spacy_model.py

# transformers is already installed above (shared by spacy-transformers and embedding model)

# Verify spaCy model loads from path
RUN python -c "import spacy; nlp = spacy.load('/app/models/en_legal_ner_trf'); print('Legal NER model OK')"

# Verify transformers works
RUN python -c "from transformers import AutoTokenizer, AutoModel; print('Transformers import OK')"

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

# Pre-download the embedding model
RUN python -c "from transformers import AutoTokenizer, AutoModel; AutoTokenizer.from_pretrained('sentence-transformers/all-MiniLM-L6-v2'); AutoModel.from_pretrained('sentence-transformers/all-MiniLM-L6-v2'); print('Embedding model OK')"
