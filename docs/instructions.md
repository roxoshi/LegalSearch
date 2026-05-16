# Taxbot — Execution Plan

## Overview
Extend the Taxbot platform with a full legal reference library: Acts, Rules,
Notifications, and Circulars. Each document type is stored in structured JSON
files under `.data/`. Documents cross-reference each other via hyperlinks in
their `html` content (e.g. `explore-notification/1000868`). These links must be
parsed, stored relationally, and made clickable in the frontend.

---

## Data Inventory (confirmed from source files)

| Type | Files | Records | Has `html` | `content_id` prefix | `primary_id` type |
|---|---|---|---|---|---|
| Acts | 5 | ~287 | Yes | `1111xxxxxxx` | `INTEGER` |
| Rules | 2 | ~243 | Yes | `122xxxxxxx` | `INTEGER` |
| Notifications | 8 | ~1,111 | No | `150xxxxxxx` | `INTEGER` |
| Circulars | 3 | ~270 | No | `160xxxxxxx` | `INTEGER` |

### JSON Field Reference

**Acts** (`acts/*.json`)
```
act_name, chapter_no, chapter_name, section_no, section_name,
content, html, source_url, primary_id (int), content_id (int)
```

**Rules** (`rules/*.json`)
```
act_name, chapter_id (int), section_no, section_name,
content, html, source_url, primary_id (int), content_id (int)
```

**Notifications** (`notifications/jsons/*.json`)
```
primary_id (int), content_id (int), notification_no, date (ISO-8601+TZ),
title, content, is_active (bool), is_amended (bool),
meta: { category, year, doc_path, order_id }
```

**Circulars** (`circulars/jsons/*.json`)
```
primary_id (int), content_id (int), circular_no (str, e.g. "1/1/2017"),
date (ISO-8601+TZ), subject, content, is_active (bool), is_amended (bool),
meta: { category, year, doc_path, order_id }
```

> Note: `circular_no` is a human-readable string like "1/1/2017".
> `primary_id` is now a plain integer for all four types — no special-casing needed.

### Cross-reference link format (in `html` of Acts and Rules only)
```
.../content-page/explore-notification/{primary_id}
.../content-page/explore-rule/{primary_id}
.../content-page/explore-act/{primary_id}
.../content-page/explore-circular/{primary_id}
```

---

## Steps

### STEP 0 — Create Planning File
- **Status:** [DONE]
- Create `docs/instructions.md` as checklist and execution tracker.

---

### STEP 1 — Database Schema Design
- **Status:** [DONE]
- Analyse JSON structures in `.data/` ✓
- Propose tables: `acts`, `rules`, `notifications`, `circulars`, `cross_references`
- Define columns, PKs, indexes
- Propose cross-reference parsing strategy
- **Gate:** Wait for approval before proceeding

#### Proposed Schema

```sql
-- ──────────────────────────────────────────────────────
-- 1. ACTS  (each row = one section of one act)
-- ──────────────────────────────────────────────────────
CREATE TABLE acts (
    id           SERIAL      PRIMARY KEY,
    primary_id   INTEGER     NOT NULL,
    content_id   BIGINT      NOT NULL UNIQUE,   -- prefix 1111x
    act_name     TEXT        NOT NULL,          -- "CGST Act", "IGST Act", …
    chapter_no   TEXT,                          -- "Chapter I"
    chapter_name TEXT,                          -- "Preliminary"
    section_no   TEXT        NOT NULL,          -- "Section 1"
    section_name TEXT,
    content      TEXT,                          -- plain text (for FTS / search)
    html_content TEXT,                          -- raw HTML (for rendering)
    source_url   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_acts_primary_id ON acts (primary_id);
CREATE INDEX idx_acts_act_name   ON acts (act_name);
CREATE INDEX idx_acts_chapter    ON acts (act_name, chapter_no);
CREATE INDEX idx_acts_fts        ON acts
    USING GIN (to_tsvector('english', coalesce(content, '')));


-- ──────────────────────────────────────────────────────
-- 2. RULES  (each row = one rule / sub-rule)
-- ──────────────────────────────────────────────────────
CREATE TABLE rules (
    id           SERIAL      PRIMARY KEY,
    primary_id   INTEGER     NOT NULL,
    content_id   BIGINT      NOT NULL UNIQUE,   -- prefix 122x
    act_name     TEXT        NOT NULL,          -- "CGST Rules", "IGST Rules"
    chapter_id   INTEGER,                       -- raw chapter reference from source
    section_no   TEXT        NOT NULL,          -- "Rule 1"
    section_name TEXT,
    content      TEXT,
    html_content TEXT,
    source_url   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rules_primary_id ON rules (primary_id);
CREATE INDEX idx_rules_act_name   ON rules (act_name);
CREATE INDEX idx_rules_fts        ON rules
    USING GIN (to_tsvector('english', coalesce(content, '')));


-- ──────────────────────────────────────────────────────
-- 3. NOTIFICATIONS
-- ──────────────────────────────────────────────────────
CREATE TABLE notifications (
    id              SERIAL      PRIMARY KEY,
    primary_id      INTEGER     NOT NULL UNIQUE,  -- used in cross-ref URLs
    content_id      BIGINT      NOT NULL UNIQUE,  -- prefix 150x
    notification_no TEXT        NOT NULL,         -- "75/2017-Central Tax"
    issued_on       TIMESTAMPTZ,
    title           TEXT,
    content         TEXT,
    category        TEXT,                         -- "Central Tax", "Central Tax Rate", …
    year            INTEGER,
    doc_path        TEXT,
    order_id        BIGINT,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    is_amended      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_notif_category ON notifications (category);
CREATE INDEX idx_notif_year     ON notifications (year);
CREATE INDEX idx_notif_fts      ON notifications
    USING GIN (to_tsvector('english',
        coalesce(title, '') || ' ' || coalesce(content, '')));


-- ──────────────────────────────────────────────────────
-- 4. CIRCULARS
-- primary_id is now INTEGER (same pattern as other types)
-- circular_no remains a human-readable string e.g. "1/1/2017"
-- ──────────────────────────────────────────────────────
CREATE TABLE circulars (
    id          SERIAL      PRIMARY KEY,
    primary_id  INTEGER     NOT NULL UNIQUE,  -- used in cross-ref URLs
    content_id  BIGINT      NOT NULL UNIQUE,  -- prefix 160x
    circular_no TEXT        NOT NULL,         -- "1/1/2017"
    issued_on   TIMESTAMPTZ,
    subject     TEXT,
    content     TEXT,
    category    TEXT,                         -- "CGST", "IGST", "CESS"
    year        INTEGER,
    doc_path    TEXT,
    order_id    BIGINT,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    is_amended  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_circulars_primary_id ON circulars (primary_id);
CREATE INDEX idx_circulars_category   ON circulars (category);
CREATE INDEX idx_circulars_year       ON circulars (year);
CREATE INDEX idx_circulars_fts        ON circulars
    USING GIN (to_tsvector('english',
        coalesce(subject, '') || ' ' || coalesce(content, '')));


-- ──────────────────────────────────────────────────────
-- 5. CROSS_REFERENCES
-- Parsed from html_content of acts and rules.
-- All primary_ids are now integers — no special-casing.
-- ──────────────────────────────────────────────────────
CREATE TYPE doc_type AS ENUM ('act', 'rule', 'notification', 'circular');

CREATE TABLE cross_references (
    id             SERIAL   PRIMARY KEY,
    source_type    doc_type NOT NULL,
    source_id      INTEGER  NOT NULL,  -- acts.primary_id or rules.primary_id
    target_type    doc_type NOT NULL,
    target_id      INTEGER  NOT NULL,  -- primary_id of target document
    anchor_text    TEXT,               -- visible link text
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source_type, source_id, target_type, target_id)
);

CREATE INDEX idx_xref_source ON cross_references (source_type, source_id);
CREATE INDEX idx_xref_target ON cross_references (target_type, target_id);
```

#### Design decisions

| Decision | Rationale |
|---|---|
| Separate tables per type | Distinct schemas; cross-type joins only via `cross_references` |
| `primary_id` uniform INTEGER | All four types now use integer primary_ids — no workarounds needed |
| `content_id` unique globally | Prefix encodes type; useful for unified search later |
| `meta` fields flattened | `category`, `year`, `doc_path`, `order_id` stored as columns for indexed filtering |
| `doc_type` enum | Prevents invalid values; self-documenting |
| GIN full-text indexes | Fast `tsquery` search per type without an extra search engine |
| No FK across tables in `cross_references` | Referential integrity across 4 tables impractical; validated at ingestion time |
| Coexists with existing tables | `acts`, `rules`, etc. are additive — `documents`, `users`, etc. untouched |

---

### STEP 2 — Schema Implementation
- **Status:** [DONE]
- Added `Act`, `Rule`, `Notification`, `Circular`, `CrossReference`, `DocType` to `backend/app/models.py`
- GIN full-text indexes defined in `__table_args__` for all four document tables
- `cross_references` has unique constraint on `(source_type, source_id, target_type, target_id)`
- Imports verified clean
- **Gate:** Wait for approval before proceeding

---

### STEP 3 — Data Ingestion Pipeline
- **Status:** [DONE]
- `etl/ingest_legal_library.py` written and dry-run verified
- Reads all 18 JSON files; normalises fields (flattens `meta`, `html`→`html_content`, `date`→`issued_on`)
- Upserts via `ON CONFLICT (content_id) DO UPDATE` — idempotent
- Regex parses `explore-{type}/{id}` links from `html_content`; inserts via `ON CONFLICT DO NOTHING`
- Dry-run counts: 287 acts, 243 rules, 1 111 notifications, 270 circulars, 1 041 cross-references
- Run: `python -m etl.ingest_legal_library [--data-dir .data] [--dry-run]`
- **Gate:** Wait for approval before proceeding

---

### STEP 4 — Backend APIs
- **Status:** [DONE]
- `GET /library/acts` — list acts (filter by act_name, chapter_no)
- `GET /library/acts/{primary_id}` — single section + outbound cross-references resolved
- `GET /library/rules/{primary_id}` — single rule + cross-references
- `GET /library/notifications/{primary_id}` — single notification
- `GET /library/circulars/{primary_id}` — single circular
- `GET /library/resolve/{type}/{primary_id}` — generic resolver (used by frontend link handler)
- `GET /library/search` — full-text search across all four types (plainto_tsquery, per-type GIN index, `types` filter param)
- **Gate:** Wait for approval before proceeding

---

### STEP 5 — Frontend Integration
- **Status:** [DONE]
- `frontend/src/lib/library.ts` — typed API client (fetchActs, resolveLibraryDoc, librarySearch + shared types)
- `frontend/src/components/Sidebar.tsx` — added BookOpenIcon → `/library`
- `frontend/src/components/CrossRefModal.tsx` — modal for cross-reference preview (Escape/backdrop to close, open-full-page link)
- `frontend/src/app/library/page.tsx` — browse/search page (type filter checkboxes, FTS via `/library/search`, result cards)
- `frontend/src/app/library/[type]/[id]/page.tsx` — unified detail page; rendered HTML for acts/rules with click-intercept for `explore-{type}/{id}` links; metadata sidebar; cross-references panel (click → modal)
- **Gate:** Wait for approval before proceeding

---

### STEP 6 — Testing & Verification
- **Status:** [DONE]
- `backend/tests/conftest.py` — added `library_act`, `library_rule`, `library_notification`, `library_circular`, `library_cross_ref` fixtures (all postgres_db-backed, skip when PG unavailable)
- `backend/tests/test_api.py` — 24 new library endpoint tests covering all 7 routes (list acts, get act/rule/notification/circular, resolve, FTS search); 404s, filter params, cross-reference resolution, response shapes
- `etl/tests/test_ingest_legal_library.py` — 14 new ingestion tests: dry-run counts for all 4 types, cross-reference parsing, xref regex unit tests, field validation, integer type assertions, missing-dir graceful handling
- **Data quality findings:** rules have duplicate content_ids across files (handled by upsert); some notifications have `null` content_id (documented in test, DB NOT NULL constraint rejects those rows)
- Run: `pytest etl/tests/test_ingest_legal_library.py backend/tests/test_api.py -v`
- Result: 16 passed, 32 skipped (postgres-dependent tests skip without test DB)

---

## Notes
- New tables coexist with existing `documents` / `document_chunks` (case law) tables
- PostgreSQL + pgvector already in use — no database engine change needed
- All ingestion scripts must be idempotent (safe to re-run)
- Cross-reference links appear only in `html` of Acts and Rules; Notifications and Circulars have plain `content` only
