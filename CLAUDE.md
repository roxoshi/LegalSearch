# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LegalSearch is a semantic search platform for GST (Goods and Services Tax) legal judgments. It uses vector embeddings with PostgreSQL/pgvector for similarity search across legal documents.

## Architecture

```
Frontend (Next.js 16, React 19, Tailwind) → Backend (FastAPI, SQLAlchemy) → PostgreSQL + pgvector
                                                    ↑
                              Data Pipeline: Shard → Filter (ML/NER) → ETL
```

**Key Components:**
- **backend/app/main.py** - FastAPI with `/search` (vector similarity) and `/document/{id}` endpoints
- **backend/app/models.py** - SQLAlchemy models: `Document` (metadata) and `DocumentChunk` (384-dim embeddings)
- **frontend/src/app/page.tsx** - Search UI with court/year filters
- **pipelines/** - PDF extraction and ML-based GST relevance filtering using spaCy NER
- **etl/** - Transform JSON+PDFs into chunked, embedded documents

## Common Commands

### Docker (Full Stack)
```bash
docker-compose up -d                          # Start db, backend, frontend
docker-compose --profile ingest up            # Run data ingestion pipeline
docker-compose --profile ingest run shard     # Extract PDFs from zips
docker-compose --profile ingest run filter    # ML-based filtering (GPU)
docker-compose --profile ingest run etl       # Transform and load to DB
```

### Local Development
```bash
# Backend
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Frontend
cd frontend && npm install && npm run dev

# ETL
python -m etl.ingest --json-dir /path/to/json --pdf-dir /path/to/pdfs
```

### Testing
```bash
# Backend tests (SQLite in-memory)
pytest backend/tests -v

# With PostgreSQL (requires docker-compose.test.yml)
docker-compose -f docker-compose.test.yml up -d
pytest backend/tests -v

# Single test
pytest backend/tests/test_api.py::test_search_endpoint_basic -v

# Pipeline tests
pytest pipelines/tests -v
```

## Key Patterns

- **Vector Search**: Query → SentenceTransformer embedding (all-MiniLM-L6-v2) → pgvector cosine_distance → top-5 unique documents
- **Chunking**: RecursiveCharacterTextSplitter with 4000 char chunks, 600 char overlap
- **Database**: Unique constraint on `documents.case_id`; upsert = delete old + insert new
- **ML Pipeline**: `en_legal_ner_trf` spaCy model extracts provisions/statutes; falls back to keyword matching

## Environment Variables

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/search_db
MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
INGEST_BATCH_SIZE=50
NEXT_PUBLIC_API_URL=http://localhost:8000  # Frontend
```
