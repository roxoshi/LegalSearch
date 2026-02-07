# Base image for ML/filter service with PyTorch and spaCy pre-installed
# Build once: docker build -f docker/base-filter.Dockerfile -t legalsearch-base-filter .

FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/0.9.26/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

# Pre-install heavy ML dependencies
RUN uv pip install --system \
    spacy>=3.7.0 \
    beautifulsoup4 \
    pymupdf \
    pdfplumber

# Pre-install the legal NER model (this is the slowest part!)
RUN pip install https://huggingface.co/opennyaiorg/en_legal_ner_trf/resolve/main/en_legal_ner_trf-any-py3-none-any.whl
