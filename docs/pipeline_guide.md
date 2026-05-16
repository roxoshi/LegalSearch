# LegalSearch Pipeline Guide

End-to-end guide for building or rebuilding the LegalSearch database from scratch.

---

## Overview

The data flow has four distinct stages:

```
Stage 1: Shard     — extract PDFs from zip/tar archives, run NER filter
Stage 2: Filter    — (part of Stage 1 via unified_pipeline.py)
Stage 3: Analyze   — send PDFs to Gemini to produce 10-field JSON per case
Stage 4: ETL       — embed JSON fields, write documents + chunks to PostgreSQL
```

Stages 1–2 are handled by `pipelines/unified_pipeline.py`.
Stage 3 is handled by `scripts/gemini_batch_analyze.py` (external LLM step).
Stage 4 is handled by `etl/ingest.py`.

---

## Prerequisites

- Docker + docker-compose installed
- Environment file at `envs/.env.staging` (or `envs/.env.production`) with:
  ```
  POSTGRES_USER=...
  POSTGRES_PASSWORD=...
  POSTGRES_DB=search_db
  DB_PORT=5432
  GEMINI_API_KEY=...          # for Stage 3
  ```
- Source data in `.data/`:
  - `.data/GST_judgments/` — SC zip files (`SC-GST-{year}.zip`) and HC tar archives
  - `.data/metadata/` — parquet files with case metadata

---

## Starting Fresh (All Stages)

### Stage 1 & 2: Shard + Filter

Extract PDFs from archives and filter for GST relevance using NER:

```bash
# Docker (full run, all courts)
docker-compose --profile ingest run filter

# Or locally
uv run python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/ \
  --judgments-dir .data/GST_judgments/ \
  --court-type all

# SC only, specific years
uv run python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/ \
  --judgments-dir .data/GST_judgments/ \
  --court-type sc \
  --years 2020 2021 2022
```

Output: `.data/gst_pdfs/{case_id}.pdf` — one PDF per filtered case.

**Two-stage mode** (filter on one machine, embed on another):
```bash
# Machine 1: shard + filter + export
uv run python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/ \
  --judgments-dir .data/GST_judgments/ \
  --export-after-filter \
  --export-dir .data/export

# Machine 2: import pre-filtered PDFs
uv run python -m pipelines.unified_pipeline \
  --import-filtered \
  --import-dir .data/export
```

---

### Stage 3: LLM Analysis (Gemini)

Send each filtered PDF to Gemini to produce structured 10-field JSON:

```bash
uv run python scripts/gemini_batch_analyze.py \
  --pdf-dir .data/gst_pdfs/ \
  --output-dir .data/batch_analysis/successes/
```

Output: `.data/batch_analysis/successes/{case_id}.json` with 10 fields:
`summary`, `facts`, `issues`, `petitioner_arguments`, `respondent_arguments`,
`analysis_of_law`, `precedent_analysis`, `courts_reasoning`, `conclusion`, `ratio_decidendi`.

---

### Stage 4: ETL Ingest

Embed the JSON fields and load documents into PostgreSQL:

```bash
# Start the database first
docker-compose up -d db

# Run ETL
uv run python -m etl.ingest \
  --analysis-dir .data/batch_analysis/successes/ \
  --metadata-dir .data/metadata/           # optional: enriches title/court/judge

# Or via Docker profile
docker-compose --profile ingest run etl
```

This creates:
- One row in `documents` per JSON file (with generated HTML `display_content`)
- Up to 10 rows in `document_chunks` per document (one per non-empty analysis field)

---

## Starting from Existing `gst_pdfs/`

If `.data/gst_pdfs/` is already populated (PDFs already filtered), skip Stages 1–2
and go straight to Stage 3:

```bash
uv run python scripts/gemini_batch_analyze.py \
  --pdf-dir .data/gst_pdfs/ \
  --output-dir .data/batch_analysis/successes/

uv run python -m etl.ingest \
  --analysis-dir .data/batch_analysis/successes/ \
  --metadata-dir .data/metadata/
```

---

## Starting from Existing Analysis JSONs

If `.data/batch_analysis/successes/` already contains JSON files, skip Stages 1–3
and run only Stage 4:

```bash
uv run python -m etl.ingest \
  --analysis-dir .data/batch_analysis/successes/ \
  --metadata-dir .data/metadata/ \
  --limit 100    # optional: process only first 100 for testing
```

---

## Running the Full Stack

```bash
# Start all services
docker-compose up -d

# Backend available at http://localhost:8000
# Frontend available at http://localhost:3000
```

Or locally for development:

```bash
# Terminal 1: Backend
cd backend && uvicorn app.main:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
```

---

## Verification Checklist

1. **Search works**: open http://localhost:3000, search "GST cancellation of registration"
   - Expect up to 10 results
2. **Document page works**: click a result → HTML sections render correctly
   - Check that all section headings appear (Summary, Facts, Issues, …)
3. **PDF download works**: click "Download PDF" → PDF opens in browser
   - Returns 404 if PDF not in `.data/gst_pdfs/`
4. **Run tests**:
   ```bash
   pytest backend/tests -v
   pytest etl/tests -v
   ```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | built from `POSTGRES_*` vars | PostgreSQL connection string |
| `MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `PDF_DIR` | `.data/gst_pdfs` | Directory where PDFs are served from |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend URL (frontend) |

---

## Directory Layout

```
.data/
  GST_judgments/          # Source zip/tar archives
  metadata/               # Parquet metadata files (SC and HC)
  gst_pdfs/               # Filtered PDFs (one per case, named {case_id}.pdf)
  batch_analysis/
    successes/            # LLM-produced JSON files (one per case)
    failures/             # Cases where Gemini analysis failed
  export/                 # Intermediate export from pipeline (two-stage mode)
    export.jsonl
    pdfs/
```
