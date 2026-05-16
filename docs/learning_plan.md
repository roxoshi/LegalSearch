# LegalSearch — Codebase Learning Plan

1 hour/day. ~5 weeks. Check off each day as you complete it.

---

## Week 1 — Data & the Core Idea

**Goal**: Know what a document looks like at each stage of its life.

- [ ] Day 1 — Read `etl/schemas.py` + `etl/transform.py` — how a judgment becomes chunks
- [ ] Day 2 — Read `backend/app/models.py` — what's actually stored in Postgres
- [ ] Day 3 — Run `psql` and `SELECT * FROM documents LIMIT 5; SELECT * FROM document_chunks LIMIT 3;` — touch the real data
- [ ] Day 4 — Read `etl/ingest.py` — the CLI entry point, end to end
- [ ] Day 5 — Trace one document: pick a `case_id` from the DB and find it in `.data/batch_analysis/successes/`

---

## Week 2 — Backend (the API)

**Goal**: Understand every HTTP endpoint and how vector search works.

- [ ] Day 1 — Read `backend/app/main.py` top to bottom
- [ ] Day 2 — Run the backend locally, hit `/search` with `curl` or Postman, read the SQL it generates in logs
- [ ] Day 3 — Read `backend/app/auth.py` — how sessions/JWT work
- [ ] Day 4 — Read `backend/tests/test_api.py` — the tests are the best spec
- [ ] Day 5 — Run `pytest backend/tests -v` and make sure you can explain each test

---

## Week 3 — Frontend

**Goal**: Know how the UI fetches and renders results.

- [ ] Day 1 — Read `frontend/src/app/page.tsx` — the main search page
- [ ] Day 2 — Read `frontend/src/context/AuthContext.tsx` + `frontend/src/components/Providers.tsx`
- [ ] Day 3 — Read `frontend/src/components/SearchResult.tsx`
- [ ] Day 4 — Open browser devtools → Network tab → do a search → inspect the actual request/response
- [ ] Day 5 — Read `frontend/src/app/document/[id]/page.tsx`

---

## Week 4 — Pipelines (how PDFs become data)

**Goal**: Understand the ingestion pipeline from raw archives to `.data/gst_pdfs/`.

- [ ] Day 1 — Read `pipelines/unified_pipeline.py` — shard → filter → NER
- [ ] Day 2 — Read `pipelines/optimizations.py` — keyword prefilter logic
- [ ] Day 3 — Read `etl/annotate_acts.py` or `etl/chunk_acts.py` (whichever feels less familiar)
- [ ] Day 4 — Run `pytest pipelines/tests -v etl/tests -v` and read the known failures
- [ ] Day 5 — Re-read `docs/technical_overview.md` (or MEMORY.md in `.claude/`) — a map of decisions made

---

## Week 5 — Infrastructure & the Whole Picture

**Goal**: Understand how everything connects and be able to explain the system end to end.

- [ ] Day 1 — Read `docker-compose.yml` — understand how services connect
- [ ] Day 2 — Read `backend/app/database.py` + `etl/database.py` — connection setup
- [ ] Day 3 — Draw the data flow yourself on paper: PDF → pipeline → DB → API → UI
- [ ] Day 4 — Pick any open ticket from `docs/` and trace exactly which files would need to change
- [ ] Day 5 — Recap: can you explain the system to someone in 5 minutes?

---

## Tips

- After each file, close it and write one sentence: "this file does X." If you can't, re-read.
- The tests are the best spec — every test is a working example of the code in action.
- Skip `library_chunks` / notifications in Week 4 — that's a recent add-on, not core.
- After Week 5, pick a small ticket and own it end to end without asking Claude first.
