# LegalSearch — Technical Overview

## Table of Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [System Architecture](#2-system-architecture)
3. [Data Flow (End-to-End)](#3-data-flow-end-to-end)
4. [Database Layer](#4-database-layer)
5. [Backend API](#5-backend-api)
6. [Frontend](#6-frontend)
7. [ETL Pipeline](#7-etl-pipeline)
8. [Data Pipelines (Ingest & Analysis)](#8-data-pipelines-ingest--analysis)
9. [Authentication System](#9-authentication-system)
10. [Deployment & Configuration](#10-deployment--configuration)
11. [Testing](#11-testing)
12. [Key Design Decisions & Gotchas](#12-key-design-decisions--gotchas)

---

## 1. Purpose & Scope

LegalSearch is a semantic search platform for **GST (Goods and Services Tax) legal judgments** from Indian courts. Users can search across thousands of judgments using natural-language queries; results are ranked by vector similarity rather than keyword matching.

**Courts covered:** Supreme Court (SC), High Courts (HC)

**Current state (as of 2026-03-07):** 7,010 documents, 66,064 chunks indexed and searchable.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│              Next.js 16 + React 19 + Tailwind 4                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP / REST
┌────────────────────────────▼────────────────────────────────────┐
│                       BACKEND API                               │
│               FastAPI + SQLAlchemy + SentenceTransformer        │
└────────────────────────────┬────────────────────────────────────┘
                             │ SQLAlchemy ORM
┌────────────────────────────▼────────────────────────────────────┐
│                        DATABASE                                 │
│               PostgreSQL 16 + pgvector extension                │
│         (documents, document_chunks, users, otp_codes)          │
└────────────────────────────▲────────────────────────────────────┘
                             │ upsert via etl/ingest.py
┌────────────────────────────┴────────────────────────────────────┐
│                        ETL LAYER                                │
│    etl/ingest.py → transform.py → schemas.py → embeddings       │
└────────────────────────────▲────────────────────────────────────┘
                             │ structured JSON input
┌────────────────────────────┴────────────────────────────────────┐
│                     LLM ANALYSIS                                │
│   pipelines/llm_analyze.py  (Anthropic / OpenAI / Gemini)       │
│   pipelines/gemini_batch_analyze.py  (async Gemini batch)       │
└────────────────────────────▲────────────────────────────────────┘
                             │ filtered PDFs
┌────────────────────────────┴────────────────────────────────────┐
│                   UNIFIED DATA PIPELINE                         │
│   Shard (extract PDFs) → Filter (keyword + NER) → Persist       │
│   pipelines/unified_pipeline.py + shard_hc.py + optimizations   │
└────────────────────────────▲────────────────────────────────────┘
                             │
              Raw Data: parquet metadata + zip/tar PDFs
```

---

## 3. Data Flow (End-to-End)

```
RAW DATA
├── SC-GST-{year}.zip          (flat zips containing PDFs)
├── HC-GST-{year}/             (hierarchical tar archives per court/bench)
└── Parquet metadata files     (.data/metadata/raw/)

  │
  ▼  STEP 1 — SHARD  (pipelines/unified_pipeline.py)
Extract PDF bytes + text from zips/tars, normalize metadata
SC uses ProcessPoolExecutor (4 workers); HC streams tars, writes PDFs to staging dir

  │
  ▼  STEP 2 — FILTER  (pipelines/unified_pipeline.py + optimizations.py)
Stage 1: Keyword pre-filter (GST_KEYWORDS, GST_PATTERNS) — eliminates ~80%
Stage 2: spaCy NER (en_legal_ner_trf) extracts PROVISION/STATUTE entities
Output: GST-relevant PDFs only

  │
  ▼  STEP 3 — PERSIST  (pipelines/unified_pipeline.py)
Write PDFs to .data/gst_pdfs/{case_id}.pdf
Optional: export metadata to .data/export/ (for pipeline resumption)

  │
  ▼  STEP 4 — LLM ANALYSIS  (pipelines/llm_analyze.py)
Per-PDF: extract text (PyMuPDF) → prompt LLM → parse 10-field JSON
Output: .data/batch_analysis/successes/{case_id}.json
Failures: .data/batch_analysis/failures/{case_id}_{error_type}.log
Alternative: Gemini Batch API (async, 50% cheaper, ~24h turnaround)

  │
  ▼  STEP 5 — ETL INGEST  (etl/ingest.py)
For each JSON file:
  1. Validate against AnalysisJSON schema (10 required fields)
  2. Look up parquet metadata by case_id (SC) or cnr (HC)
  3. Generate HTML display_content from 10 analysis fields
  4. Create ≤10 DocumentChunks (one per non-empty analysis field)
  5. Batch-embed all chunks (SentenceTransformer all-MiniLM-L6-v2, 384 dims)
  6. Upsert to DB: delete old doc + chunks, insert new

  │
  ▼  SEARCH / RETRIEVAL  (backend/app/main.py)
User query → embedding → pgvector cosine_distance → top-100 candidates
→ deduplicate to ≤10 unique documents → return to frontend
```

---

## 4. Database Layer

### Schema

```
users
├── id (UUID, PK)
├── first_name, last_name, year_of_birth (nullable)
└── created_at

user_identities
├── id (UUID, PK)
├── user_id (FK → users.id)
├── provider (email | google)
├── provider_id (email address or Google sub)
├── is_verified (bool)
└── UNIQUE(provider, provider_id)

otp_codes
├── id (UUID, PK)
├── identity_id (FK → user_identities.id)
├── code_hash (SHA256 of 6-digit OTP)
├── expires_at (5-minute window)
├── attempts (max 5)
└── is_used (bool)

documents
├── id (UUID, PK)
├── case_id (TEXT, UNIQUE) ← core identifier
├── title, petitioner, respondent, judge, citation, court, decision_date
├── content (TEXT) ← full concatenated text
├── display_content (TEXT) ← rendered HTML
├── is_gst_core (BOOL) ← ML classification
├── extracted_provisions, extracted_statutes (ARRAY[TEXT])
└── created_at, updated_at

document_chunks
├── id (UUID, PK)
├── document_id (FK → documents.id, CASCADE DELETE)
├── chunk_content (TEXT)
└── embedding (VECTOR(384)) ← pgvector
```

### Indexes

```sql
-- Vector similarity (HNSW — approximate, fast)
CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- Full-text search fallback
CREATE INDEX ON documents USING gin (to_tsvector('english', content));

-- Incremental sync
CREATE INDEX ON documents (updated_at);
```

### File Locations

| File | Role |
|------|------|
| `backend/app/models.py` | SQLAlchemy ORM models |
| `backend/app/database.py` | Engine + session factory, URL building |
| `etl/database.py` | Standalone DB init for ETL (same models) |
| `db/init/01-enable-pgvector.sql` | Extension + index creation (run once by Docker) |

### Important: Special Characters in DB Password

Both `backend/app/database.py` and `etl/database.py` use `urllib.parse.quote_plus()` when constructing `DATABASE_URL` from individual env vars. This is required because passwords may contain `+` or other special characters that break connection string parsing.

---

## 5. Backend API

**Entry point:** `backend/app/main.py`
**Framework:** FastAPI with SQLAlchemy sync sessions
**Embedding model:** `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions), loaded once at startup

### Endpoints

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/search` | No | Vector similarity search |
| GET | `/document/{id}` | No | Full document metadata + content |
| GET | `/document/{id}/pdf` | No | Serve original PDF file |
| GET | `/document/{id}/summary` | No | First 1000 chars of content |
| POST | `/auth/request-otp` | No | Send 6-digit OTP to email |
| POST | `/auth/verify-otp` | No | Verify OTP, set auth cookie |
| POST | `/auth/google` | No | Google OAuth token exchange |
| GET | `/auth/me` | Yes | Current user info |
| POST | `/auth/profile` | Yes | Update user profile |
| POST | `/auth/logout` | Yes | Clear auth cookie |
| POST | `/auth/dev-login` | No (staging only) | Bypass auth for testing |

### Search Logic

```python
# Simplified
query_embedding = model.encode(q)                    # 384-dim vector
chunks = session.query(DocumentChunk)
    .order_by(cosine_distance(embedding, query_embedding))
    .filter(/* optional court/year/judge/date filters */)
    .limit(100)
    .all()

# Deduplicate: take first occurrence per document_id
seen = set()
results = []
for chunk in chunks:
    if chunk.document_id not in seen:
        seen.add(chunk.document_id)
        results.append(chunk.document)
    if len(results) == 10:
        break
```

### CORS Configuration

Three modes controlled by `ENVIRONMENT` env var:

- **`dev`**: Allow all HTTP origins (`allow_origins=["*"]`)
- **`staging`**: Explicit origins + private-network regex (`192.168.x.x`, `10.x.x.x`, `172.16-31.x.x`, `localhost`)
- **`prod`**: Explicit `FRONTEND_URL` only

> **Note:** FastAPI/Starlette supports both `allow_origins` and `allow_origin_regex` simultaneously. The global exception handler (`@app.exception_handler(Exception)`) is wired inside CORS middleware so error responses also include CORS headers.

### PDF Serving

`GET /document/{id}/pdf` resolves: `{PDF_DIR}/{case_id}.pdf`
`PDF_DIR` defaults to `.data/gst_pdfs/`. Returns 404 if the file doesn't exist.

---

## 6. Frontend

**Framework:** Next.js 16 (App Router), React 19, Tailwind CSS 4
**API communication:** All calls use `credentials: 'include'` (cookie-based auth)

### Pages

| Route | File | Description |
|-------|------|-------------|
| `/` | `src/app/page.tsx` | Search page with filters |
| `/document/[id]` | `src/app/document/[id]/page.tsx` | Judgment detail view |
| `/login` | `src/app/login/page.tsx` | 3-step login flow |

### Search Page (`page.tsx`)

- URL-driven state (`?q=...&court=...&year=...&judge=...&is_gst=...&decision_date=...`)
- Filter dropdowns: Court, GST Core (Y/N), Judge, Decision Date
- Results rendered as cards with smooth fade-in animation
- `getApiUrl()` dynamically resolves API base URL (supports `NEXT_PUBLIC_API_PORT`)

### Login Flow

```
Step 1: Email input → POST /auth/request-otp
Step 2: 6-digit OTP (numeric, masked) → POST /auth/verify-otp
Step 3: Optional profile (firstName, lastName, yearOfBirth) → POST /auth/profile
```

- Google OAuth renders only if `NEXT_PUBLIC_GOOGLE_CLIENT_ID` is set (avoids LAN GSI errors)
- Dev Login button shows if `NEXT_PUBLIC_ENABLE_DEV_LOGIN=true`

### Auth Context (`context/AuthContext.tsx`)

Provides to all pages:
- `user` — current user object or `null`
- `isLoading` — auth fetch in progress
- `requestOtp(email)`, `verifyOtp(email, code, profile?)`, `loginWithGoogle(token)`, `devLogin()`, `updateProfile(data)`, `logout()`

Auto-fetches `/auth/me` on mount to restore session from cookie.

### Environment Variables (Frontend)

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXT_PUBLIC_API_URL` | No | Full API base URL (overrides port-based logic) |
| `NEXT_PUBLIC_API_PORT` | No | Port for dynamic API URL resolution |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | No | Enables Google OAuth button |
| `NEXT_PUBLIC_ENABLE_DEV_LOGIN` | No | Shows dev login button |

---

## 7. ETL Pipeline

The ETL layer takes LLM-analyzed JSON files and loads them into the database. It is the final step before data becomes searchable.

### Schema (`etl/schemas.py`)

**AnalysisJSON** — 10 required string fields (mirrors LLM output):

| Field | Description |
|-------|-------------|
| `summary` | Case overview |
| `facts` | Material facts |
| `issues` | Legal issues raised |
| `petitioner_arguments` | Arguments for petitioner |
| `respondent_arguments` | Arguments for respondent |
| `analysis_of_law` | Court's legal analysis |
| `precedent_analysis` | Cited precedents |
| `courts_reasoning` | Reasoning and rationale |
| `conclusion` | Outcome and order |
| `ratio_decidendi` | Binding principle of law |

**MetadataJSON** — Optional, populated from parquet: `case_id`, `title`, `petitioner`, `respondent`, `judge`, `citation`, `court`, `decision_date`

### Transform (`etl/transform.py`)

`Transformer.process_document(case_id, analysis, metadata)` produces:
1. One **Document** row with HTML `display_content` (all 10 fields rendered as HTML sections)
2. Up to **10 DocumentChunk** rows — one per non-empty analysis field (skips `""`, `"Not Mentioned"`, `"n/a"`, `"none"`)
3. All chunk embeddings computed in a single `model.encode()` batch call

### Ingest CLI (`etl/ingest.py`)

```bash
python -m etl.ingest \
  --analysis-dir .data/batch_analysis/successes/ \
  --metadata-dir .data/metadata/raw/ \
  [--limit 0]     # 0 = all files
```

- Discovers all `.json` files recursively under `--analysis-dir`
- Loads parquet metadata (SC by `case_id` col, HC by `cnr` col) — slow (~7 min for 11.3M records)
- Upsert strategy: `DELETE FROM documents WHERE case_id = ?` then `INSERT` (cascade deletes chunks)
- Logs per-file success/failure; prints final counts

---

## 8. Data Pipelines (Ingest & Analysis)

### Unified Pipeline (`pipelines/unified_pipeline.py`)

Handles Steps 1–3: shard, filter, persist.

```bash
python -m pipelines.unified_pipeline \
  --metadata-dir .data/metadata/raw \
  --judgments-dir .data/GST_judgments \
  --court-type all \          # all | sc | hc
  [--limit 1000] \
  [--skip-filter] \
  [--export-after-filter --export-dir .data/export]
```

**SC extraction:** `pipelines/shard_judgments.py`
- Opens `SC-GST-{year}.zip` via `ProcessPoolExecutor` (4 workers)
- Finds PDF by basename; tries `_EN` suffix variant
- Extracts both text and raw PDF bytes

**HC extraction:** `pipelines/shard_hc.py`
- Groups records by tar file path, opens each tar once
- Handles `~` → `_` court code mapping in directory names
- Writes PDFs to `.data/staging/hc_pdfs/` (not held in memory — avoids OOM)
- Error log: `.data/logs/hc_extraction_errors.log`
- `parse_title_parties(title)` splits on "Vs"/"vs."/"V/s" into petitioner/respondent

**NER Filtering:** `pipelines/optimizations.py`
- Stage 1: Keyword pre-filter (20+ GST keywords + regex patterns) — eliminates ~80% cheaply
- Stage 2: `spaCy en_legal_ner_trf` runs only on candidates; extracts `PROVISION` and `STATUTE` entities
- Sets `doc.is_gst_core = True` on matches

### LLM Analysis (`pipelines/llm_analyze.py`)

```bash
python -m pipelines.llm_analyze \
  --input .data/gst_pdfs/ \
  --provider anthropic \
  --model claude-opus-4-6 \
  --output .data/batch_analysis \
  [--max-tokens 4096]
```

Per-PDF workflow:
1. Extract text via PyMuPDF (10–50x faster than pdfplumber; `fitz.TOOLS.mupdf_display_errors(False)` silences noise)
2. Compose prompt from `PROMPT.md` template + extracted text
3. Call LLM provider API
4. Strip markdown fences, parse JSON, validate against `CaseLawAnalysis` schema
5. Write to `successes/` or `failures/` with error type suffix

### LLM Providers (`pipelines/llm_providers.py`)

Factory: `get_provider(name, **kwargs)` returns one of:

| Provider | Default Model | Env Var |
|----------|---------------|---------|
| `anthropic` | `claude-opus-4-6` | `ANTHROPIC_API_KEY` |
| `openai` | `gpt-4o` | `OPENAI_API_KEY` |
| `google` | `gemini-3-flash-preview` | `GOOGLE_API_KEY` |

### Gemini Batch Analysis (`pipelines/gemini_batch_analyze.py`)

Alternative to per-PDF sync analysis:
- Submits all PDFs as an async batch job to Gemini Batch API
- ~50% cost reduction vs. synchronous API
- ~24-hour turnaround
- Supports both AI Studio (API key) and Vertex AI (ADC) authentication
- Saves batch job ID for resumable polling

---

## 9. Authentication System

### Flow

```
Email OTP Flow:
  Client → POST /auth/request-otp (email)
    → generate 6-digit OTP
    → SHA256 hash stored in otp_codes table (5-min expiry, 5 max attempts)
    → send plaintext code via SMTP
  Client → POST /auth/verify-otp (email, code, optional profile)
    → verify hash match + not expired + attempts < 5
    → create/update user + identity
    → issue JWT in HttpOnly cookie

Google OAuth Flow:
  Client → POST /auth/google (id_token from Google)
    → verify via google.oauth2.id_token
    → create/update user + identity
    → issue JWT in HttpOnly cookie

Dev Login (staging only, ENABLE_DEV_LOGIN=true):
  Client → POST /auth/dev-login (email)
    → creates/reuses user@test.local
    → issue JWT in HttpOnly cookie immediately
```

### JWT Cookie

```
Name:      access_token
Algorithm: HS256
Expiry:    24 hours
HttpOnly:  true
Secure:    auto (true unless dev; false allows HTTP)
SameSite:  lax (HTTPS) | omitted (HTTP, fixes Safari cross-port rejection)
```

### Key Files

| File | Role |
|------|------|
| `backend/app/auth.py` | OTP generation/hashing, JWT creation, SMTP sending, Google token verify |
| `backend/app/models.py` | User, UserIdentity, OTPCode ORM models |
| `backend/app/main.py` | Auth route handlers |
| `frontend/src/context/AuthContext.tsx` | Client-side auth state + methods |

---

## 10. Deployment & Configuration

### Docker Compose Services

| Service | Image | Ports | Profile |
|---------|-------|-------|---------|
| `db` | `postgres:16` | 5432 | default |
| `backend` | `./backend/Dockerfile` | 8000 | default |
| `frontend` | `./frontend/Dockerfile` | 3000 | default |
| `mailpit` | `axllent/mailpit` | 1025/8025 | `mail` |
| `etl` | `./etl` | — | `ingest` |
| `pipelines` | `./pipelines` | — | `ingest` |

### Common Docker Commands

```bash
# Start full stack
docker-compose up -d

# Run ETL ingest
docker-compose --profile ingest run etl

# Run pipeline steps
docker-compose --profile ingest run shard    # extract PDFs
docker-compose --profile ingest run filter   # ML filtering (needs GPU)
```

### Required Environment Variables

| Variable | Used By | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | backend, etl | DB username |
| `POSTGRES_PASSWORD` | backend, etl | DB password (special chars OK) |
| `POSTGRES_DB` | backend, etl | Database name |
| `POSTGRES_HOST` | backend, etl | DB hostname |
| `SECRET_KEY` | backend | JWT signing key |
| `ENVIRONMENT` | backend | `dev` / `staging` / `prod` |
| `FRONTEND_URL` | backend | CORS origin for prod |
| `SMTP_HOST` | backend | Mail server |
| `SMTP_USER` | backend | Mail username |
| `SMTP_PASSWORD` | backend | Mail password |
| `SMTP_FROM_EMAIL` | backend | Sender address |
| `GOOGLE_CLIENT_ID` | backend | Google OAuth verification |
| `COOKIE_SECURE` | backend | `false` to disable secure flag (HTTP staging) |
| `ENABLE_DEV_LOGIN` | backend | `true` enables `/auth/dev-login` |
| `PDF_DIR` | backend | Where PDFs are served from (default `.data/gst_pdfs/`) |

### Re-Ingest Command

```bash
set -a && source envs/.env.staging && set +a
uv run python -m etl.ingest \
  --analysis-dir .data/batch_analysis/successes/ \
  --metadata-dir .data/metadata/raw/
```

---

## 11. Testing

### Test Suites

| Suite | Runner | Notes |
|-------|--------|-------|
| `backend/tests/` | pytest | Uses SQLite in-memory; no Docker needed |
| `etl/tests/` | pytest (uv) | Unit tests for schemas, transform, ingest |
| `pipelines/tests/` | pytest (uv) | Pipeline unit + integration tests |

```bash
# Run all tests
uv run pytest etl/tests/ backend/tests/ pipelines/tests/ -v

# Backend with real PostgreSQL
docker-compose -f docker-compose.test.yml up -d
pytest backend/tests -v
```

### Known Pre-existing Failures (as of 2026-03-07)

- `test_optimizations.py::TestKeywordPrefilter` — 6 failures
- `test_optimizations.py::TestIntegration::test_keyword_filter_reduces_ner_load` — 1 failure
- `test_shard_parity.py` — multiple failures
- `test_filter_judgments.py` — multiple failures

### Testing Patterns & Gotchas

- `ProcessPoolExecutor` tests cannot use `unittest.mock.patch` on worker functions (pickling). Use real temp files or mock at a higher level.
- Backend tests stub out SentenceTransformer to avoid loading the model during test runs.

---

## 12. Key Design Decisions & Gotchas

### Why JSON-based Chunking (not RecursiveCharacterTextSplitter)

Each judgment is chunked by **analysis field** (one chunk per field), not by character count. This gives semantically coherent chunks aligned to the structure of a legal judgment. The LLM does the heavy lifting of segmentation at analysis time.

### Why Delete + Re-insert for Upserts

SQLAlchemy ORM lacks native `INSERT ... ON CONFLICT DO UPDATE` for composite objects. The `case_id` UNIQUE constraint means we delete the document (which cascades to chunks) and re-insert. This is acceptable because re-ingest is a batch operation, not a hot path.

### Why pgvector HNSW (not IVFFlat)

HNSW provides approximate nearest-neighbor search with no training phase — no need to run `VACUUM` or rebuild the index after inserts. Better fit for a dataset that grows via periodic re-ingests.

### Why Individual Env Vars (not DATABASE_URL) in Docker

Docker environment variable injection does not URL-encode values. A password containing `+` would break a pre-composed `DATABASE_URL`. Both `backend/app/database.py` and `etl/database.py` call `quote_plus()` to build the URL at runtime.

### HC vs SC Data Format Differences

| Aspect | Supreme Court (SC) | High Courts (HC) |
|--------|-------------------|-----------------|
| Archive format | Flat `.zip` | Hierarchical `.tar` per court/bench |
| Primary identifier | `case_id` column | `cnr` column |
| PDF path prefix | Direct filename | `./` prefix in tar entries |
| Court code separator | N/A | `~` in parquet, `_` in directory names |
| Memory strategy | PDF bytes in memory | Written to staging dir (OOM risk otherwise) |

### CORS on Error Responses

FastAPI's default `ServerErrorMiddleware` sits **outside** `CORSMiddleware`. Unhandled 500s would strip CORS headers, causing the browser to report a network error instead of the actual failure. The global `@app.exception_handler(Exception)` forces errors through the middleware stack from the inside, preserving CORS headers.

### Safari Cookie Behaviour on LAN

Safari rejects `SameSite=None` cookies over HTTP (cross-port IP access). The fix is to **omit** `SameSite` entirely when `COOKIE_SECURE=false`. This is set in `set_auth_cookie()` in `backend/app/auth.py`.

### Two-Stage NER Filtering

Running `en_legal_ner_trf` (transformer-based) on every document is expensive. A keyword pre-filter on raw text first eliminates ~80% of documents cheaply, so NER only runs on ~20% of the corpus — a significant throughput improvement.

### Deleted Files (no callers)

- `pipelines/convert_pdf.py` — removed; PDF extraction is now done inline via PyMuPDF in `llm_analyze.py` and `unified_pipeline.py`
- `pipelines/tests/test_convert_pdf.py` — removed with the above
