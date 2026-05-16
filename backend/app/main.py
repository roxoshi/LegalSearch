import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from . import models
from .auth import (
    GoogleAuthRequest,
    OTPRequest,
    OTPVerify,
    ProfileUpdate,
    SubscriptionResponse,
    UserResponse,
    clear_auth_cookie,
    create_access_token,
    generate_otp,
    get_current_user_required,
    get_or_create_identity,
    hash_otp,
    is_email,
    send_otp_email,
    set_auth_cookie,
    verify_google_id_token,
    FREE_TRIAL_DAYS,
    OTP_EXPIRY_MINUTES,
    OTP_MAX_ATTEMPTS,
)
from .custom_types import PartyOptions, SearchResult
from .database import engine, get_database
from .embeddings import EmbeddingModel
from .models import (
    Act,
    ActChunk,
    Circular,
    CrossReference,
    DocType,
    Document,
    DocumentChunk,
    Notification,
    OTPCode,
    Rule,
    RuleChunk,
    SubscriptionPlan,
    SubscriptionStatus,
    User,
    UserIdentity,
    UserSubscription,
)


@asynccontextmanager
async def lifespan(app):
    models.Base.metadata.create_all(bind=engine)
    # Ensure act_sections column exists (added by etl/extract_act_sections.py migration)
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS act_sections text[]"
        ))
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS primary_provisions text[]"
        ))
        conn.execute(text(
            "ALTER TABLE acts ADD COLUMN IF NOT EXISTS embedding vector(768)"
        ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS act_chunks (
                id SERIAL PRIMARY KEY,
                act_id INTEGER NOT NULL REFERENCES acts(id) ON DELETE CASCADE,
                sub_section_label TEXT NOT NULL,
                chunk_content TEXT NOT NULL,
                embedding vector(768)
            )
        """))
        conn.execute(text(
            "ALTER TABLE act_chunks ADD COLUMN IF NOT EXISTS embedding_text TEXT"
        ))
        conn.commit()
    yield


app = FastAPI(lifespan=lifespan)

PDF_DIR = Path(os.getenv("PDF_DIR", ".data/gst_pdfs"))
ACTS_DIR = Path(os.getenv("ACTS_DIR", ".data/acts"))
NOTIF_PDF_DIR = Path(os.getenv("NOTIF_PDF_DIR", ".data/notifications/pdfs"))
CIRCULAR_PDF_DIR = Path(os.getenv("CIRCULAR_PDF_DIR", ".data/circulars/pdfs"))

_NOTIF_CATEGORY_DIR: dict[str, str] = {
    "Central Tax": "central_tax",
    "Central Tax (Rate)": "central_tax_rate",
    "Compensation Cess": "compensation_cess",
    "Compensation Cess (Rate)": "compensation_cess_rate",
    "Integrated Tax": "integrated_tax",
    "Integrated Tax (Rate)": "integrated_tax_rate",
    "Union Territory Tax": "union_territory_tax",
    "Union Territory Tax (Rate)": "union_territory_tax_rate",
}

_CIRCULAR_CATEGORY_DIR: dict[str, str] = {
    "CGST": "cgst",
    "IGST": "igst",
    "CESS": "cess",
}


def _find_notif_pdf(base_dir: Path, category: str | None, year: int | None, notif_no: str) -> Path | None:
    import re as _re
    cat_subdir = _NOTIF_CATEGORY_DIR.get(category or "", "")
    cat_dir = base_dir / cat_subdir if cat_subdir else base_dir
    m = _re.match(r"(\d+)", notif_no.strip())
    if not m:
        return None
    num = m.group(1).zfill(2)
    year_dirs = [cat_dir / str(year)] if year and (cat_dir / str(year)).is_dir() else (
        sorted(cat_dir.iterdir()) if cat_dir.is_dir() else []
    )
    for ydir in year_dirs:
        if not ydir.is_dir():
            continue
        for pdf in sorted(ydir.glob(f"{num}_*.pdf")):
            return pdf
        for pdf in sorted(ydir.glob(f"{int(num)}_*.pdf")):
            return pdf
    return None


def _find_circular_pdf(base_dir: Path, category: str | None, year: int | None, circular_no: str) -> Path | None:
    import re as _re
    cat_subdir = _CIRCULAR_CATEGORY_DIR.get(category or "", "")
    cat_dir = base_dir / cat_subdir if cat_subdir else base_dir
    m = _re.match(r"(\d+)", circular_no.strip())
    if not m:
        return None
    num = m.group(1).zfill(2)
    year_dirs = [cat_dir / str(year)] if year and (cat_dir / str(year)).is_dir() else (
        sorted(cat_dir.iterdir()) if cat_dir.is_dir() else []
    )
    for ydir in year_dirs:
        if not ydir.is_dir():
            continue
        for pdf in sorted(ydir.glob(f"{num}_*.pdf")):
            return pdf
        for pdf in sorted(ydir.glob(f"{int(num)}_*.pdf")):
            return pdf
    # Flat layout (no year subdirs)
    if cat_dir.is_dir():
        for pdf in sorted(cat_dir.glob(f"{num}_*.pdf")):
            return pdf
    return None

environment = os.getenv("ENVIRONMENT", "development")

allowed_origins = [
    "http://localhost:3000",
]
frontend_url = os.getenv("FRONTEND_URL", "")
if frontend_url:
    allowed_origins.append(frontend_url)

# Private network regex — covers 192.168.x.x, 10.x.x.x, 172.16-31.x.x, localhost.
# Starlette checks allow_origins AND allow_origin_regex independently, so both can coexist.
_LAN_ORIGIN_REGEX = (
    r"https?://(localhost|127\.0\.0\.1"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
    r"(:\d+)?"
)

# In dev: allow any HTTP origin so LAN IPs work (mirrors api.ts dynamic hostname logic).
# In staging/prod: restrict to explicit allowed_origins + always allow private-network IPs
# so docker-compose / LAN access works without extra env-var configuration.
_cors_kwargs: dict = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if environment in ("development", "dev"):
    _cors_kwargs["allow_origin_regex"] = r"http://.*"
else:
    _cors_kwargs["allow_origins"] = allowed_origins
    _cors_kwargs["allow_origin_regex"] = _LAN_ORIGIN_REGEX

app.add_middleware(CORSMiddleware, **_cors_kwargs)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all so unhandled errors (e.g. DB failures) still go through the
    CORS middleware and the browser receives a proper CORS-annotated response."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


embed_model = EmbeddingModel(
    os.getenv("MODEL_NAME", "sentence-transformers/all-mpnet-base-v2")
)



try:
    from .reranker import CrossEncoderReranker
    reranker: CrossEncoderReranker | None = CrossEncoderReranker(
        os.getenv("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    )
except Exception as _reranker_err:
    logger.warning("Cross-encoder not loaded (%s). Re-ranking disabled.", _reranker_err)
    reranker = None


import re


def _court_authority_bonus(court: str) -> float:
    """Return +0.015 for Supreme Court judgments, else 0."""
    if "supreme court" in (court or "").lower():
        return 0.015
    return 0.0


def _recency_bonus(decision_date: str) -> float:
    """Linear recency bonus: 0 for pre-GST, up to +0.01 for current year."""
    year_match = re.search(r"\b(20\d{2})\b", decision_date or "")
    if not year_match:
        return 0.0
    year = int(year_match.group(1))
    gst_start, current_year = 2017, 2026
    if year < gst_start:
        return 0.0
    return 0.01 * (year - gst_start) / (current_year - gst_start)


def _apply_doc_filters(stmt, court, year, judge, is_gst, decision_date, in_favour=None, act_filter=None, section_filter=None):
    """Apply shared document-level filters to a select statement."""
    if court:
        court_norm = re.sub(r'\s+', ' ', court.strip())
        stmt = stmt.where(func.regexp_replace(Document.court, r'\s+', ' ', 'g').ilike(f"%{court_norm}%"))
    if year:
        stmt = stmt.where(Document.decision_date.like(f"%{year}%"))
    if judge:
        stmt = stmt.where(Document.judge.ilike(f"%{judge}%"))
    if is_gst is not None and is_gst.lower() in ("true", "false", "yes", "no"):
        stmt = stmt.where(Document.is_gst_core == (is_gst.lower() in ("true", "yes")))
    if decision_date:
        stmt = stmt.where(Document.decision_date.like(f"{decision_date}%"))
    if in_favour:
        key = in_favour.lower()
        if key == "in favour of assessee":
            stmt = stmt.where(
                Document.disposal_nature.ilike("%allowed%"),
                ~Document.disposal_nature.ilike("%partly%"),
            )
        elif key == "in favour of revenue":
            stmt = stmt.where(Document.disposal_nature.ilike("%dismiss%"))
        elif key == "partly in favour of assessee":
            stmt = stmt.where(Document.disposal_nature.ilike("%partly%"))
        elif key == "not available":
            stmt = stmt.where(
                (Document.disposal_nature == None) | (Document.disposal_nature == "")
            )
    if act_filter and section_filter:
        # Exact pair match
        stmt = stmt.where(
            text("EXISTS (SELECT 1 FROM unnest(act_sections) AS s WHERE s = :pair)")
            .bindparams(pair=f"{section_filter}|{act_filter}")
        )
    elif act_filter:
        stmt = stmt.where(
            text("EXISTS (SELECT 1 FROM unnest(act_sections) AS s WHERE s LIKE :af_pattern)")
            .bindparams(af_pattern=f"%|{act_filter}")
        )
    return stmt


def _browse_documents(
    court, year, judge, is_gst, decision_date, db: Session,
    in_favour=None, petitioner=None, respondent=None,
    date_from=None, date_to=None, act_filter=None, section_filter=None,
    offset: int = 0, limit: int = 10,
) -> list[tuple]:
    """Return (chunk, doc) for browse mode (no query): first chunk per doc, sorted by recency."""
    doc_stmt = select(Document)
    doc_stmt = _apply_doc_filters(doc_stmt, court, year, judge, is_gst, decision_date, in_favour, act_filter, section_filter)
    if petitioner:
        doc_stmt = doc_stmt.where(Document.petitioner.ilike(f"%{petitioner}%"))
    if respondent:
        doc_stmt = doc_stmt.where(Document.respondent.ilike(f"%{respondent}%"))
    if date_from:
        doc_stmt = doc_stmt.where(Document.decision_date >= date_from)
    if date_to:
        doc_stmt = doc_stmt.where(Document.decision_date <= date_to)
    doc_stmt = doc_stmt.order_by(Document.decision_date.desc()).offset(offset).limit(limit)
    docs = db.execute(doc_stmt).scalars().all()

    results = []
    for doc in docs:
        chunk = db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc.id)
            .limit(1)
        ).scalar_one_or_none()
        if chunk:
            results.append((chunk, doc))
    return results


def _vector_rankings(q: str, court, year, judge, is_gst, decision_date, db: Session, in_favour=None, act_filter=None, section_filter=None) -> list[tuple]:
    """Return (chunk, doc) ordered by cosine distance, up to 200 rows."""
    query_vector = embed_model.encode(q).tolist()
    stmt = select(DocumentChunk, Document).join(
        Document, DocumentChunk.document_id == Document.id
    )
    stmt = _apply_doc_filters(stmt, court, year, judge, is_gst, decision_date, in_favour, act_filter, section_filter)
    stmt = stmt.order_by(DocumentChunk.embedding.cosine_distance(query_vector)).limit(200)
    return db.execute(stmt).all()


def _fts_rankings(q: str, court, year, judge, is_gst, decision_date, db: Session, in_favour=None, act_filter=None, section_filter=None) -> list[tuple]:
    """Return (chunk, doc, rank) ordered by ts_rank, up to 300 rows."""
    tsq = func.plainto_tsquery("english", q)
    fts_vec = func.to_tsvector("english", DocumentChunk.chunk_content)
    stmt = (
        select(DocumentChunk, Document, func.ts_rank(fts_vec, tsq).label("rank"))
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(fts_vec.op("@@")(tsq))
    )
    stmt = _apply_doc_filters(stmt, court, year, judge, is_gst, decision_date, in_favour, act_filter, section_filter)
    stmt = stmt.order_by(text("rank DESC")).limit(300)
    return db.execute(stmt).all()


def rrf(rankings: list[dict[int, int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for doc_id, rank in ranking.items():
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
    return scores


@app.get("/search/parties")
def search_parties(
    court: str = Query(None),
    in_favour: str = Query(None),
    db: Session = Depends(get_database),
) -> PartyOptions:
    """Return all unique petitioners and respondents from the corpus (optionally filtered by court/in_favour)."""
    stmt = select(Document.petitioner, Document.respondent)
    if court:
        court_norm = re.sub(r'\s+', ' ', court.strip())
        stmt = stmt.where(func.regexp_replace(Document.court, r'\s+', ' ', 'g').ilike(f"%{court_norm}%"))
    if in_favour:
        key = in_favour.lower()
        if key == "in favour of assessee":
            stmt = stmt.where(Document.disposal_nature.ilike("%allowed%"), ~Document.disposal_nature.ilike("%partly%"))
        elif key == "in favour of revenue":
            stmt = stmt.where(Document.disposal_nature.ilike("%dismiss%"))
        elif key == "partly in favour of assessee":
            stmt = stmt.where(Document.disposal_nature.ilike("%partly%"))
        elif key == "not available":
            stmt = stmt.where((Document.disposal_nature == None) | (Document.disposal_nature == ""))
    rows = db.execute(stmt).all()
    petitioners = sorted({r[0].strip() for r in rows if r[0] and r[0].strip()})
    respondents = sorted({r[1].strip() for r in rows if r[1] and r[1].strip()})
    return PartyOptions(petitioners=petitioners, respondents=respondents)


@app.get("/search/acts")
def search_acts(db: Session = Depends(get_database)) -> list[str]:
    """Return canonical Act/Rules names present in the corpus, ordered by frequency."""
    rows = db.execute(
        text("""
            SELECT regexp_replace(s, '^.*\\|', '') AS act, COUNT(*) AS cnt
            FROM documents, unnest(act_sections) AS s
            WHERE act_sections IS NOT NULL
            GROUP BY act
            ORDER BY cnt DESC
        """)
    ).all()
    return [r[0] for r in rows if r[0]]


@app.get("/search/sections")
def search_sections(
    act: str = Query(...),
    db: Session = Depends(get_database),
) -> list[str]:
    """Return sections/rules cited under the given act, ordered by frequency."""
    rows = db.execute(
        text("""
            SELECT regexp_replace(s, '\\|.*$', '') AS section, COUNT(*) AS cnt
            FROM documents, unnest(act_sections) AS s
            WHERE s LIKE :pattern
            GROUP BY section
            ORDER BY cnt DESC
        """),
        {"pattern": f"%|{act}"},
    ).all()
    return [r[0] for r in rows if r[0]]


def _build_result(chunk, doc, score: float) -> SearchResult:
    return SearchResult(
        id=doc.id,
        chunk_id=chunk.id,
        case_id=doc.case_id,
        title=doc.title,
        citation=doc.citation,
        content=chunk.chunk_content,
        court=doc.court,
        decision_date=doc.decision_date,
        rrf_score=score,
    )


_RERANK_POOL = 30  # cross-encoder sees this many candidates


@app.get("/search")
def vector_search(
    q: str = Query(None),
    court: str = Query(None),
    year: str = Query(None),
    judge: str = Query(None),
    is_gst: str = Query(None),
    decision_date: str = Query(None),
    in_favour: str = Query(None),
    petitioner: str = Query(None),
    respondent: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    act_filter: str = Query(None),
    section_filter: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_database),
) -> list[SearchResult]:
    offset = (page - 1) * page_size

    # ── Browse mode: no query, return docs sorted by recency ──────────────────
    if not q or not q.strip():
        rows = _browse_documents(
            court, year, judge, is_gst, decision_date, db,
            in_favour, petitioner, respondent,
            date_from=date_from, date_to=date_to, act_filter=act_filter, section_filter=section_filter,
            offset=offset, limit=page_size,
        )
        return [_build_result(chunk, doc, 0.0) for chunk, doc in rows]

    # ── Run both retrieval strategies ─────────────────────────────────────────
    vec_rows = _vector_rankings(q, court, year, judge, is_gst, decision_date, db, in_favour, act_filter, section_filter)
    fts_rows = _fts_rankings(q, court, year, judge, is_gst, decision_date, db, in_favour, act_filter, section_filter)

    def best_chunk_per_doc(rows) -> tuple[dict[int, int], dict[int, tuple]]:
        ranking: dict[int, int] = {}
        best: dict[int, tuple] = {}
        rank = 1
        for row in rows:
            chunk, doc = row[0], row[1]
            if doc.id not in ranking:
                ranking[doc.id] = rank
                best[doc.id] = (chunk, doc)
                rank += 1
        return ranking, best

    vec_ranking, vec_best = best_chunk_per_doc(vec_rows)
    fts_ranking, fts_best = best_chunk_per_doc(fts_rows)

    all_doc_ids = set(vec_ranking) | set(fts_ranking)
    scores = rrf([vec_ranking, fts_ranking])

    # ── SC authority boost + recency boost ────────────────────────────────────
    all_docs = {**fts_best, **vec_best}
    for doc_id, (chunk, doc) in all_docs.items():
        scores[doc_id] = (
            scores.get(doc_id, 0)
            + _court_authority_bonus(doc.court)
            + _recency_bonus(doc.decision_date)
        )

    # ── Sort + cross-encoder re-ranking ───────────────────────────────────────
    sorted_ids = sorted(all_doc_ids, key=lambda d: scores.get(d, 0), reverse=True)

    candidates = sorted_ids[:_RERANK_POOL]
    if reranker is not None and candidates:
        cd_map: dict[int, tuple] = {
            did: (vec_best.get(did) or fts_best.get(did))
            for did in candidates
        }
        passages = [cd_map[did][0].chunk_content[:500] for did in candidates]
        ce_scores = reranker.rank(q, passages)
        ce_score_map = dict(zip(candidates, ce_scores))
        candidates.sort(key=lambda d: ce_score_map.get(d, 0.0), reverse=True)

    tail = sorted_ids[_RERANK_POOL:]
    final_order = candidates + tail

    # ── Post-filters (petitioner/respondent/date/act) ─────────────────────────
    # Applied after ranking so search quality is based on the query alone;
    # these narrow the ranked list rather than constraining retrieval.
    if petitioner or respondent or date_from or date_to:
        def _matches_filters(doc_id: int) -> bool:
            _, doc = vec_best.get(doc_id) or fts_best.get(doc_id)
            if petitioner and petitioner.lower() not in (doc.petitioner or "").lower():
                return False
            if respondent and respondent.lower() not in (doc.respondent or "").lower():
                return False
            if date_from and (doc.decision_date or "") < date_from:
                return False
            if date_to and (doc.decision_date or "") > date_to:
                return False
            return True
        final_order = [did for did in final_order if _matches_filters(did)]

    # ── Paginate ──────────────────────────────────────────────────────────────
    page_ids = final_order[offset: offset + page_size]

    results = []
    for doc_id in page_ids:
        chunk, doc = vec_best.get(doc_id) or fts_best.get(doc_id)
        results.append(_build_result(chunk, doc, scores.get(doc_id, 0)))

    return results



@app.get("/document/{id}")
def get_document(id: int, db: Session = Depends(get_database)):
    doc = db.query(Document).filter(Document.id == id).first()
    if not doc:
        return {"error": "Document not found"}
    return doc


@app.get("/document/{id}/pdf")
def download_pdf(id: int, db: Session = Depends(get_database)):
    doc = db.query(Document).filter(Document.id == id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    pdf_path = PDF_DIR / f"{doc.case_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not available for this case")
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=f"{doc.case_id}.pdf",
    )


# ── Navigational query detection ─────────────────────────────────────────────

import re as _re

# Matches section refs: "16", "Section 16", "Sec 16", "s.16", "17(5)", "17(5)(b)"
_NAV_SECTION_RE = _re.compile(
    r"""^\s*
    (?:section\s*|sec\.?\s*|s\.?\s*)?   # optional section prefix
    (\d+[A-Z]?)                          # section number e.g. 16, 17A
    ((?:\([^)]+\))*)                     # optional sub-sections e.g. (5)(b)
    \s*$""",
    _re.IGNORECASE | _re.VERBOSE,
)

# Matches rule refs: "Rule 86B", "rule 42", "r.86B"
_NAV_RULE_RE = _re.compile(
    r"""^\s*rule\s*\.?\s*(\d+[A-Za-z]*)\s*$""",
    _re.IGNORECASE,
)

def _nav_pin(q: str, act_name_filter: str | None, db: Session) -> list:
    """If query looks like a section/rule reference, return exact-match chunks pinned to top."""
    q = q.strip()

    # Rule reference
    rm = _NAV_RULE_RE.match(q)
    if rm:
        rule_num = rm.group(1)
        stmt = (
            select(ActChunk, Act)
            .join(Act, ActChunk.act_id == Act.id)
            .where(Act.section_no.ilike(f"Rule {rule_num}%"))
        )
        if act_name_filter:
            stmt = stmt.where(Act.act_name.ilike(f"%{act_name_filter}%"))
        stmt = stmt.order_by(ActChunk.sub_section_label).limit(5)
        return db.execute(stmt).all()

    # Section reference
    sm = _NAV_SECTION_RE.match(q)
    if not sm:
        return []
    sec_num = sm.group(1)
    sub = sm.group(2)
    label_prefix = f"Section {sec_num}{sub}"

    stmt = (
        select(ActChunk, Act)
        .join(Act, ActChunk.act_id == Act.id)
        .where(ActChunk.sub_section_label.ilike(f"{label_prefix}%"))
    )
    if act_name_filter:
        stmt = stmt.where(Act.act_name.ilike(f"%{act_name_filter}%"))
    stmt = stmt.order_by(
        Act.act_name.ilike("%cgst%").desc(),
        ActChunk.sub_section_label,
    ).limit(5)

    return db.execute(stmt).all()


# ── Static Acts (served from JSON files in ACTS_DIR) ─────────────────────────

import json as _json
import functools as _functools

# Friendly display names keyed by filename stem
_ACT_DISPLAY_NAMES: dict[str, str] = {
    "cgst_act": "CGST Act",
    "igst_act": "IGST Act",
    "utgst_act": "UTGST Act",
    "gst_compensation_act": "GST Compensation to States Act",
    "constitution_amendment_act": "Constitution (101st Amendment) Act",
}


@_functools.lru_cache(maxsize=None)
def _load_act(key: str) -> list[dict]:
    path = ACTS_DIR / f"{key}.json"
    if not path.exists():
        raise FileNotFoundError(key)
    with open(path, encoding="utf-8") as f:
        return _json.load(f)


@app.get("/acts/list")
def list_static_acts():
    """Return available acts derived from ACTS_DIR JSON files."""
    acts = []
    _EXCLUDED = {"annotations", "batch_input"}
    for path in sorted(ACTS_DIR.glob("*.json")):
        key = path.stem
        if key in _EXCLUDED or key.startswith("batch_"):
            continue
        acts.append({
            "key": key,
            "name": _ACT_DISPLAY_NAMES.get(key, key.replace("_", " ").title()),
        })
    return acts


@app.get("/acts/{key}/sections")
def list_act_sections(key: str):
    """Return the section index (no full content) for an act."""
    try:
        sections = _load_act(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Act '{key}' not found")
    return [
        {
            "idx": i,
            "chapter_no": s.get("chapter_no"),
            "chapter_name": s.get("chapter_name"),
            "section_no": s.get("section_no"),
            "section_name": s.get("section_name"),
        }
        for i, s in enumerate(sections)
    ]


@app.get("/acts/{key}/section/{idx}")
def get_act_section(key: str, idx: int):
    """Return full content for a single section by index."""
    try:
        sections = _load_act(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Act '{key}' not found")
    if idx < 0 or idx >= len(sections):
        raise HTTPException(status_code=404, detail="Section index out of range")
    return sections[idx]


@app.get("/acts/search")
def search_acts_library(
    q: str = Query(...),
    act_name: str = Query(None),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_database),
):
    """FTS + vector search over act sub-section chunks with RRF fusion.

    Searches ActChunk (one row per sub-section) for precise matching.
    act_name optionally scopes results to a specific act.
    """
    # ── Navigational pin (exact section match) ───────────────────────────────
    nav_rows = _nav_pin(q, act_name, db)
    pinned_chunk_ids: set[int] = {row.ActChunk.id for row in nav_rows}

    query_vector = embed_model.encode(q).tolist()

    def _base_stmt():
        stmt = select(ActChunk, Act).join(Act, ActChunk.act_id == Act.id)
        if act_name:
            stmt = stmt.where(Act.act_name.ilike(f"%{act_name}%"))
        return stmt

    # ── Vector ranking ────────────────────────────────────────────────────────
    vec_rows = db.execute(
        _base_stmt()
        .where(ActChunk.embedding.isnot(None))
        .order_by(ActChunk.embedding.cosine_distance(query_vector))
        .limit(100)
    ).all()
    vec_ranking: dict[int, int] = {row.ActChunk.id: rank for rank, row in enumerate(vec_rows)}

    # ── FTS ranking ───────────────────────────────────────────────────────────
    tsq = func.plainto_tsquery("english", q)
    fts_vec = func.to_tsvector("english", ActChunk.chunk_content)
    fts_rows = db.execute(
        _base_stmt()
        .where(fts_vec.op("@@")(tsq))
        .order_by(func.ts_rank(fts_vec, tsq).desc())
        .limit(150)
    ).all()
    fts_ranking: dict[int, int] = {row.ActChunk.id: rank for rank, row in enumerate(fts_rows)}

    # ── RRF fusion ────────────────────────────────────────────────────────────
    K = 60
    scores: dict[int, float] = {}
    for id_, rank in vec_ranking.items():
        scores[id_] = scores.get(id_, 0) + 1 / (K + rank)
    for id_, rank in fts_ranking.items():
        scores[id_] = scores.get(id_, 0) + 1 / (K + rank)

    # Collect all unique chunk+act rows seen
    rows_by_chunk_id: dict[int, tuple] = {row.ActChunk.id: row for row in vec_rows}
    for row in fts_rows:
        rows_by_chunk_id.setdefault(row.ActChunk.id, row)

    ranked_ids = sorted(scores, key=lambda i: scores[i], reverse=True)

    def _row_to_dict(chunk: ActChunk, act: Act, score: float) -> dict:
        raw = chunk.chunk_content
        # Strip markdown artefacts from source text (* bold, leading asterisks)
        clean = re.sub(r'\*+', '', raw)
        clean = re.sub(r'\s+', ' ', clean).strip()
        snippet = clean[:300]
        if len(clean) > 300:
            snippet += "…"
        return {
            "act_id": act.id,
            "primary_id": act.primary_id,
            "act_name": act.act_name,
            "chapter_no": act.chapter_no,
            "chapter_name": act.chapter_name,
            "section_no": act.section_no,
            "section_name": act.section_name,
            # which sub-section matched (used by frontend to scroll + highlight)
            "matched_sub_section": chunk.sub_section_label,
            "snippet": snippet,
            "rrf_score": score,
        }

    results = []
    seen_act_ids: set[int] = set()

    # Pinned navigational results first (deduplicated by act)
    for row in nav_rows:
        act_id = row.Act.id
        if act_id not in seen_act_ids:
            seen_act_ids.add(act_id)
            results.append(_row_to_dict(row.ActChunk, row.Act, 1.0))

    # RRF results — one result per section, skip already pinned
    for chunk_id in ranked_ids:
        if chunk_id in pinned_chunk_ids:
            continue
        row = rows_by_chunk_id[chunk_id]
        act_id = row.Act.id
        if act_id not in seen_act_ids:
            seen_act_ids.add(act_id)
            results.append(_row_to_dict(row.ActChunk, row.Act, scores[chunk_id]))

    return results[:limit]


@app.get("/rules/search")
def search_rules_library(
    q: str = Query(...),
    rule_name: str = Query(None),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_database),
):
    """FTS + vector search over rule sub-section chunks with RRF fusion."""
    query_vector = embed_model.encode(q).tolist()

    def _base_stmt():
        stmt = select(RuleChunk, Rule).join(Rule, RuleChunk.rule_id == Rule.id)
        if rule_name:
            stmt = stmt.where(Rule.act_name.ilike(f"%{rule_name}%"))
        return stmt

    vec_rows = db.execute(
        _base_stmt()
        .where(RuleChunk.embedding.isnot(None))
        .order_by(RuleChunk.embedding.cosine_distance(query_vector))
        .limit(100)
    ).all()
    vec_ranking: dict[int, int] = {row.RuleChunk.id: rank for rank, row in enumerate(vec_rows)}

    tsq = func.plainto_tsquery("english", q)
    fts_vec = func.to_tsvector("english", RuleChunk.chunk_content)
    fts_rows = db.execute(
        _base_stmt()
        .where(fts_vec.op("@@")(tsq))
        .order_by(func.ts_rank(fts_vec, tsq).desc())
        .limit(150)
    ).all()
    fts_ranking: dict[int, int] = {row.RuleChunk.id: rank for rank, row in enumerate(fts_rows)}

    K = 60
    scores: dict[int, float] = {}
    for id_, rank in vec_ranking.items():
        scores[id_] = scores.get(id_, 0) + 1 / (K + rank)
    for id_, rank in fts_ranking.items():
        scores[id_] = scores.get(id_, 0) + 1 / (K + rank)

    rows_by_chunk_id: dict[int, tuple] = {row.RuleChunk.id: row for row in vec_rows}
    for row in fts_rows:
        rows_by_chunk_id.setdefault(row.RuleChunk.id, row)

    ranked_ids = sorted(scores, key=lambda i: scores[i], reverse=True)

    def _row_to_dict(chunk: RuleChunk, rule: Rule, score: float) -> dict:
        raw = chunk.chunk_content
        clean = re.sub(r'\*+', '', raw)
        clean = re.sub(r'\s+', ' ', clean).strip()
        snippet = clean[:300] + ("…" if len(clean) > 300 else "")
        return {
            "rule_id": rule.id,
            "primary_id": rule.primary_id,
            "rule_name": rule.act_name,
            "section_no": rule.section_no,
            "section_name": rule.section_name,
            "matched_sub_section": chunk.sub_section_label,
            "snippet": snippet,
            "rrf_score": score,
        }

    results = []
    seen_rule_ids: set[int] = set()
    for chunk_id in ranked_ids:
        row = rows_by_chunk_id[chunk_id]
        rule_id = row.Rule.id
        if rule_id not in seen_rule_ids:
            seen_rule_ids.add(rule_id)
            results.append(_row_to_dict(row.RuleChunk, row.Rule, scores[chunk_id]))

    return results[:limit]


@app.get("/notifications/search")
def search_notifications(
    q: str = Query(...),
    category: str = Query(None),
    year: int = Query(None),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_database),
):
    """FTS search over notifications (title + content).

    Optionally scoped by category (e.g. "Central Tax") and/or year.
    Returns results ordered by FTS rank.
    """
    tsq = func.plainto_tsquery("english", q)
    fts_vec = func.to_tsvector(
        "english",
        func.coalesce(Notification.title, "") + " " + func.coalesce(Notification.content, ""),
    )

    stmt = (
        select(Notification)
        .where(fts_vec.op("@@")(tsq))
        .order_by(func.ts_rank(fts_vec, tsq).desc())
    )
    if category:
        stmt = stmt.where(Notification.category.ilike(f"%{category}%"))
    if year:
        stmt = stmt.where(Notification.year == year)
    stmt = stmt.limit(limit)

    rows = db.execute(stmt).scalars().all()

    def _snippet(text: str | None) -> str:
        if not text:
            return ""
        clean = re.sub(r'\s+', ' ', text).strip()
        return clean[:250] + ("…" if len(clean) > 250 else "")

    return [
        {
            "primary_id": r.primary_id,
            "notification_no": r.notification_no,
            "title": r.title,
            "issued_on": r.issued_on.isoformat() if r.issued_on else None,
            "category": r.category,
            "year": r.year,
            "is_amended": r.is_amended,
            "has_structured_html": r.structured_html is not None,
            "snippet": _snippet(r.content),
        }
        for r in rows
    ]


@app.get("/circulars/search")
def search_circulars(
    q: str = Query(...),
    category: str = Query(None),
    year: int = Query(None),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_database),
):
    """FTS search over circulars (subject + content)."""
    tsq = func.plainto_tsquery("english", q)
    fts_vec = func.to_tsvector(
        "english",
        func.coalesce(Circular.subject, "") + " " + func.coalesce(Circular.content, ""),
    )

    stmt = (
        select(Circular)
        .where(fts_vec.op("@@")(tsq))
        .order_by(func.ts_rank(fts_vec, tsq).desc())
    )
    if category:
        stmt = stmt.where(Circular.category.ilike(f"%{category}%"))
    if year:
        stmt = stmt.where(Circular.year == year)
    stmt = stmt.limit(limit)

    rows = db.execute(stmt).scalars().all()

    def _snippet(text: str | None) -> str:
        if not text:
            return ""
        clean = re.sub(r'\s+', ' ', text).strip()
        return clean[:250] + ("…" if len(clean) > 250 else "")

    return [
        {
            "primary_id": r.primary_id,
            "circular_no": r.circular_no,
            "subject": r.subject,
            "issued_on": r.issued_on.isoformat() if r.issued_on else None,
            "category": r.category,
            "year": r.year,
            "is_amended": r.is_amended,
            "has_structured_html": r.structured_html is not None,
            "snippet": _snippet(r.content),
        }
        for r in rows
    ]


@app.get("/document/{id}/summary")
def get_document_summary(id: int, db: Session = Depends(get_database)):
    doc = db.query(Document).filter(Document.id == id).first()
    if not doc:
        return {"error": "Document not found"}

    content = doc.content or doc.display_content or ""
    summary = content[:1000]
    if len(content) > 1000:
        summary += "..."

    return {"summary": summary}


# ==================== Authentication Endpoints ====================


@app.post("/auth/request-otp")
def request_otp(body: OTPRequest, db: Session = Depends(get_database)):
    identifier = body.identifier.strip().lower()

    if not is_email(identifier):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid email address",
        )

    # Ensure identity exists (creates user + trial if new)
    get_or_create_identity(db, identifier, "email")

    # Delete any existing OTPs for this identifier
    db.query(OTPCode).filter(OTPCode.identifier == identifier).delete()

    # Generate and store OTP
    otp = generate_otp()
    otp_record = OTPCode(
        identifier=identifier,
        otp_hash=hash_otp(otp),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES),
        attempts=0,
    )
    db.add(otp_record)
    db.commit()

    send_otp_email(identifier, otp)

    return {"message": "OTP sent", "identifier": identifier}


@app.post("/auth/verify-otp")
def verify_otp(
    body: OTPVerify,
    response: Response,
    db: Session = Depends(get_database),
):
    identifier = body.identifier.strip().lower()
    provider = "email"

    otp_record = (
        db.query(OTPCode)
        .filter(OTPCode.identifier == identifier)
        .first()
    )

    if not otp_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No OTP found. Please request a new one.",
        )

    # Check attempts
    if otp_record.attempts >= OTP_MAX_ATTEMPTS:
        db.delete(otp_record)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please request a new OTP.",
        )

    # Check expiry (handle both naive and aware datetimes for SQLite compatibility)
    expires_at = otp_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        db.delete(otp_record)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP has expired. Please request a new one.",
        )

    # Check OTP
    if hash_otp(body.otp) != otp_record.otp_hash:
        otp_record.attempts += 1
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP.",
        )

    # OTP is valid — clean up
    db.delete(otp_record)

    identity = (
        db.query(UserIdentity)
        .filter(
            UserIdentity.provider == provider,
            UserIdentity.provider_id == identifier,
        )
        .first()
    )

    if not identity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identity not found.",
        )

    identity.is_verified = True
    user = identity.user

    # Check if profile needs completion
    needs_profile = not user.first_name or not user.last_name

    # If profile data provided inline, update user
    if body.first_name and body.last_name:
        user.first_name = body.first_name
        user.last_name = body.last_name
        if body.year_of_birth:
            user.year_of_birth = body.year_of_birth
        needs_profile = False

    db.commit()

    token = create_access_token(data={"sub": str(user.id)})
    set_auth_cookie(response, token)

    sub = db.query(UserSubscription).filter(UserSubscription.user_id == user.id).first()
    return {
        "user": UserResponse.from_user(user).model_dump(),
        "needs_profile": needs_profile,
        "subscription": SubscriptionResponse.from_subscription(sub).model_dump() if sub else None,
    }


@app.post("/auth/google")
def google_auth(
    body: GoogleAuthRequest,
    response: Response,
    db: Session = Depends(get_database),
):
    idinfo = verify_google_id_token(body.credential)

    email = idinfo.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account has no email",
        )

    # Get or create google identity
    identity = (
        db.query(UserIdentity)
        .filter(
            UserIdentity.provider == "google",
            UserIdentity.provider_id == email,
        )
        .first()
    )

    if identity:
        user = identity.user
    else:
        # Check if email identity already exists (link accounts)
        email_identity = (
            db.query(UserIdentity)
            .filter(
                UserIdentity.provider == "email",
                UserIdentity.provider_id == email,
            )
            .first()
        )

        if email_identity:
            user = email_identity.user
        else:
            # Create new user + trial subscription
            from uuid import uuid4

            user = User(
                id=uuid4(),
                first_name=idinfo.get("given_name", ""),
                last_name=idinfo.get("family_name", ""),
            )
            db.add(user)
            db.flush()

            now = datetime.now(timezone.utc)
            db.add(UserSubscription(
                user_id=user.id,
                plan=SubscriptionPlan.trial,
                status=SubscriptionStatus.active,
                trial_start=now,
                trial_end=now + timedelta(days=FREE_TRIAL_DAYS),
            ))

        # Create google identity
        google_identity = UserIdentity(
            user_id=user.id,
            provider="google",
            provider_id=email,
            is_verified=True,
        )
        db.add(google_identity)
        db.commit()

    # Issue JWT
    token = create_access_token(data={"sub": str(user.id)})
    set_auth_cookie(response, token)

    needs_profile = not user.first_name or not user.last_name

    return {
        "user": UserResponse.from_user(user).model_dump(),
        "needs_profile": needs_profile,
    }


@app.post("/auth/logout")
def logout(response: Response):
    clear_auth_cookie(response)
    return {"message": "Logged out"}


@app.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user_required)):
    return UserResponse.from_user(current_user)


@app.post("/auth/profile")
def update_profile(
    body: ProfileUpdate,
    db: Session = Depends(get_database),
    current_user: User = Depends(get_current_user_required),
):
    current_user.first_name = body.first_name
    current_user.last_name = body.last_name
    if body.year_of_birth is not None:
        current_user.year_of_birth = body.year_of_birth
    db.commit()
    db.refresh(current_user)
    return UserResponse.from_user(current_user)


@app.get("/auth/subscription")
def get_subscription(
    db: Session = Depends(get_database),
    current_user: User = Depends(get_current_user_required),
):
    sub = db.query(UserSubscription).filter(UserSubscription.user_id == current_user.id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")
    return SubscriptionResponse.from_subscription(sub).model_dump()


# ==================== Staging-Only: Dev Login ====================

if os.getenv("ENVIRONMENT") == "staging":
    from pydantic import BaseModel as _BaseModel

    class DevLoginRequest(_BaseModel):
        email: str
        first_name: str = "Test"
        last_name: str = "User"

    @app.post("/auth/dev-login")
    def dev_login(
        body: DevLoginRequest,
        response: Response,
        db: Session = Depends(get_database),
    ):
        """Staging-only: bypass OTP/Google and log in directly."""
        identifier = body.email.strip().lower()
        identity, _ = get_or_create_identity(db, identifier, "email")
        identity.is_verified = True
        user = identity.user
        if not user.first_name:
            user.first_name = body.first_name
        if not user.last_name:
            user.last_name = body.last_name
        db.commit()

        token = create_access_token(data={"sub": str(user.id)})
        set_auth_cookie(response, token)
        return {
            "user": UserResponse.from_user(user).model_dump(),
            "needs_profile": False,
        }


# ==================== Legal Reference Library ====================


class CrossRefOut(BaseModel):
    target_type: str
    target_id: int
    anchor_text: str | None = None
    label: str | None = None   # section_no / notification_no / circular_no
    title: str | None = None   # section_name / title / subject


class ActListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    act_name: str
    chapter_no: str | None = None
    chapter_name: str | None = None
    section_no: str
    section_name: str | None = None


class RuleListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    act_name: str
    section_no: str
    section_name: str | None = None


class NotificationListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    notification_no: str
    title: str | None = None
    year: int | None = None
    category: str | None = None
    is_active: bool = True


class CircularListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    circular_no: str
    subject: str | None = None
    year: int | None = None
    category: str | None = None
    is_active: bool = True


class ActOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    content_id: int
    act_name: str
    chapter_no: str | None = None
    chapter_name: str | None = None
    section_no: str
    section_name: str | None = None
    content: str | None = None
    html_content: str | None = None
    source_url: str | None = None
    cross_references: list[CrossRefOut] = []


class RuleOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    content_id: int
    act_name: str
    chapter_id: int | None = None
    section_no: str
    section_name: str | None = None
    content: str | None = None
    html_content: str | None = None
    source_url: str | None = None
    cross_references: list[CrossRefOut] = []


class NotificationOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    content_id: int
    notification_no: str
    issued_on: datetime | None = None
    title: str | None = None
    content: str | None = None
    structured_html: str | None = None
    category: str | None = None
    year: int | None = None
    is_active: bool
    is_amended: bool


class CircularOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    primary_id: int
    content_id: int
    circular_no: str
    issued_on: datetime | None = None
    subject: str | None = None
    content: str | None = None
    structured_html: str | None = None
    category: str | None = None
    year: int | None = None
    is_active: bool
    is_amended: bool


class LibrarySearchHit(BaseModel):
    type: str
    primary_id: int
    label: str
    title: str | None = None
    snippet: str | None = None


def _resolve_xrefs(source_type: str, source_primary_id: int, db: Session) -> list[CrossRefOut]:
    """Fetch and resolve outbound cross-references for an Act or Rule section."""
    xrefs = (
        db.query(CrossReference)
        .filter(
            CrossReference.source_type == DocType(source_type),
            CrossReference.source_id == source_primary_id,
        )
        .all()
    )
    out = []
    for xr in xrefs:
        ttype = xr.target_type.value
        tid = xr.target_id
        label, title = None, None
        if ttype == "notification":
            row = db.query(Notification).filter(Notification.primary_id == tid).first()
            if row:
                label, title = row.notification_no, row.title
        elif ttype == "circular":
            row = db.query(Circular).filter(Circular.primary_id == tid).first()
            if row:
                label, title = row.circular_no, row.subject
        elif ttype == "act":
            row = db.query(Act).filter(Act.primary_id == tid).first()
            if row:
                label, title = row.section_no, row.section_name
        elif ttype == "rule":
            row = db.query(Rule).filter(Rule.primary_id == tid).first()
            if row:
                label, title = row.section_no, row.section_name
        out.append(CrossRefOut(
            target_type=ttype,
            target_id=tid,
            anchor_text=xr.anchor_text,
            label=label,
            title=title,
        ))
    return out


@app.get("/library/acts", response_model=list[ActListItem])
def list_acts(
    act_name: str = Query(None),
    chapter_no: str = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_database),
):
    q = db.query(Act)
    if act_name:
        q = q.filter(Act.act_name.ilike(f"%{act_name}%"))
    if chapter_no:
        q = q.filter(Act.chapter_no.ilike(f"%{chapter_no}%"))
    rows = q.order_by(Act.act_name, Act.id).limit(limit).all()
    return [ActListItem.model_validate(r) for r in rows]


@app.get("/library/rules", response_model=list[RuleListItem])
def list_rules(
    act_name: str = Query(None),
    limit: int = Query(300, le=500),
    db: Session = Depends(get_database),
):
    q = db.query(Rule)
    if act_name:
        q = q.filter(Rule.act_name.ilike(f"%{act_name}%"))
    rows = q.order_by(Rule.act_name, Rule.id).limit(limit).all()
    return [RuleListItem.model_validate(r) for r in rows]


@app.get("/library/notifications", response_model=list[NotificationListItem])
def list_notifications(
    year: int = Query(None),
    category: str = Query(None),
    limit: int = Query(300, le=1500),
    db: Session = Depends(get_database),
):
    q = db.query(Notification)
    if year:
        q = q.filter(Notification.year == year)
    if category:
        q = q.filter(Notification.category.ilike(f"%{category}%"))
    rows = q.order_by(Notification.year.desc(), Notification.id).limit(limit).all()
    return [NotificationListItem.model_validate(r) for r in rows]


@app.get("/library/circulars", response_model=list[CircularListItem])
def list_circulars(
    year: int = Query(None),
    category: str = Query(None),
    limit: int = Query(300, le=1500),
    db: Session = Depends(get_database),
):
    q = db.query(Circular)
    if year:
        q = q.filter(Circular.year == year)
    if category:
        q = q.filter(Circular.category.ilike(f"%{category}%"))
    rows = q.order_by(Circular.year.desc(), Circular.id).limit(limit).all()
    return [CircularListItem.model_validate(r) for r in rows]


@app.get("/library/acts/{primary_id}", response_model=ActOut)
def get_act(primary_id: int, db: Session = Depends(get_database)):
    row = db.query(Act).filter(Act.primary_id == primary_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Act section not found")
    out = ActOut.model_validate(row)
    out.cross_references = _resolve_xrefs("act", primary_id, db)
    return out


@app.get("/library/rules/{primary_id}", response_model=RuleOut)
def get_rule(primary_id: int, db: Session = Depends(get_database)):
    row = db.query(Rule).filter(Rule.primary_id == primary_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    out = RuleOut.model_validate(row)
    out.cross_references = _resolve_xrefs("rule", primary_id, db)
    return out


@app.get("/library/notifications/search", response_model=list[NotificationListItem])
def search_notifications_semantic(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_database),
):
    """Hybrid FTS + semantic search over notification chunks. Returns matched notifications."""
    # Vector search — outer ORDER BY score DESC so RRF sees semantic ranking, not primary_id order
    q_vec = embed_model.encode(q).tolist()
    vec_rows = db.execute(text("""
        SELECT primary_id, score FROM (
            SELECT DISTINCT ON (primary_id) primary_id,
                   1 - (embedding <=> CAST(:vec AS vector)) AS score
            FROM library_chunks
            WHERE doc_type = 'notification'
            ORDER BY primary_id, embedding <=> CAST(:vec AS vector)
        ) sub
        ORDER BY score DESC
    """), {"vec": str(q_vec)}).fetchall()

    # FTS on chunk content
    fts_rows = db.execute(text("""
        SELECT primary_id, score FROM (
            SELECT DISTINCT ON (primary_id) primary_id,
                   ts_rank(to_tsvector('english', content),
                           websearch_to_tsquery('english', :q)) AS score
            FROM library_chunks
            WHERE doc_type = 'notification'
              AND to_tsvector('english', content) @@ websearch_to_tsquery('english', :q)
            ORDER BY primary_id, ts_rank(to_tsvector('english', content),
                                         websearch_to_tsquery('english', :q)) DESC
        ) sub
        ORDER BY score DESC
    """), {"q": q}).fetchall()

    # Field-level match on notification_no + title (T1: identifier-based queries)
    field_rows = db.execute(text("""
        SELECT primary_id,
               greatest(
                 similarity(notification_no, :q),
                 similarity(coalesce(title, ''), :q)
               ) AS score
        FROM notifications
        WHERE notification_no % :q OR title % :q
           OR notification_no ILIKE :like OR title ILIKE :like
        ORDER BY score DESC
        LIMIT 50
    """), {"q": q, "like": f"%{q}%"}).fetchall()

    # Field lane gets 3× weight; identifier hits with similarity > 0.65 are pinned to front
    FIELD_WEIGHT = 3.0
    pinned = [row[0] for row in field_rows if row[1] >= 0.65]
    primary_ids = _rrf_library([vec_rows, fts_rows, field_rows], limit * 3,
                               weights=[1.0, 1.0, FIELD_WEIGHT])
    if not primary_ids:
        return []

    # Move pinned exact-match IDs to the front, preserving their relative order
    pinned_set = set(pinned)
    primary_ids = pinned + [pid for pid in primary_ids if pid not in pinned_set]

    rows = db.query(Notification).filter(Notification.primary_id.in_(primary_ids[:limit * 3])).all()
    id_to_row = {r.primary_id: r for r in rows}

    # Recency boost + inactive penalty (withdrawn docs sink to bottom)
    GST_START, CURRENT_YEAR = 2017, 2026
    rrf_score = {pid: 1.0 / (60 + rank + 1) for rank, pid in enumerate(primary_ids)}
    def _notif_boosted(pid: int) -> float:
        row = id_to_row.get(pid)
        year = row.year if row and row.year else GST_START
        recency = max(0.0, (year - GST_START) / (CURRENT_YEAR - GST_START))
        active_mult = 1.0 if (not row or row.is_active) else 0.1
        return rrf_score.get(pid, 0.0) * (1.0 + 0.15 * recency) * active_mult

    ranked = sorted((pid for pid in primary_ids if pid in id_to_row),
                    key=_notif_boosted, reverse=True)
    # Re-apply identifier pins after sort so exact number matches always surface
    if pinned:
        pinned_present = [p for p in pinned if p in id_to_row]
        pinned_set2 = set(pinned_present)
        ranked = (pinned_present + [p for p in ranked if p not in pinned_set2])[:limit]
    else:
        ranked = ranked[:limit]
    return [NotificationListItem.model_validate(id_to_row[pid]) for pid in ranked]


@app.get("/library/circulars/search", response_model=list[CircularListItem])
def search_circulars_semantic(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_database),
):
    """Hybrid FTS + semantic search over circular chunks. Returns matched circulars."""
    q_vec = embed_model.encode(q).tolist()
    vec_rows = db.execute(text("""
        SELECT primary_id, score FROM (
            SELECT DISTINCT ON (primary_id) primary_id,
                   1 - (embedding <=> CAST(:vec AS vector)) AS score
            FROM library_chunks
            WHERE doc_type = 'circular'
            ORDER BY primary_id, embedding <=> CAST(:vec AS vector)
        ) sub
        ORDER BY score DESC
    """), {"vec": str(q_vec)}).fetchall()

    fts_rows = db.execute(text("""
        SELECT primary_id, score FROM (
            SELECT DISTINCT ON (primary_id) primary_id,
                   ts_rank(to_tsvector('english', content),
                           websearch_to_tsquery('english', :q)) AS score
            FROM library_chunks
            WHERE doc_type = 'circular'
              AND to_tsvector('english', content) @@ websearch_to_tsquery('english', :q)
            ORDER BY primary_id, ts_rank(to_tsvector('english', content),
                                         websearch_to_tsquery('english', :q)) DESC
        ) sub
        ORDER BY score DESC
    """), {"q": q}).fetchall()

    # Field-level match on circular_no + subject (T1: identifier-based queries)
    field_rows = db.execute(text("""
        SELECT primary_id,
               greatest(
                 similarity(circular_no, :q),
                 similarity(coalesce(subject, ''), :q)
               ) AS score
        FROM circulars
        WHERE circular_no % :q OR subject % :q
           OR circular_no ILIKE :like OR subject ILIKE :like
        ORDER BY score DESC
        LIMIT 50
    """), {"q": q, "like": f"%{q}%"}).fetchall()

    FIELD_WEIGHT = 3.0
    pinned = [row[0] for row in field_rows if row[1] >= 0.65]
    primary_ids = _rrf_library([vec_rows, fts_rows, field_rows], limit * 3,
                               weights=[1.0, 1.0, FIELD_WEIGHT])
    if not primary_ids:
        return []

    pinned_set = set(pinned)
    primary_ids = pinned + [pid for pid in primary_ids if pid not in pinned_set]

    rows = db.query(Circular).filter(Circular.primary_id.in_(primary_ids[:limit * 3])).all()
    id_to_row = {r.primary_id: r for r in rows}

    # Recency boost + inactive penalty (withdrawn docs sink to bottom)
    GST_START, CURRENT_YEAR = 2017, 2026
    rrf_score = {pid: 1.0 / (60 + rank + 1) for rank, pid in enumerate(primary_ids)}
    def _circ_boosted(pid: int) -> float:
        row = id_to_row.get(pid)
        year = row.year if row and row.year else GST_START
        recency = max(0.0, (year - GST_START) / (CURRENT_YEAR - GST_START))
        active_mult = 1.0 if (not row or row.is_active) else 0.1
        return rrf_score.get(pid, 0.0) * (1.0 + 0.15 * recency) * active_mult

    ranked = sorted((pid for pid in primary_ids if pid in id_to_row),
                    key=_circ_boosted, reverse=True)
    if pinned:
        pinned_present = [p for p in pinned if p in id_to_row]
        pinned_set2 = set(pinned_present)
        ranked = (pinned_present + [p for p in ranked if p not in pinned_set2])[:limit]
    else:
        ranked = ranked[:limit]
    return [CircularListItem.model_validate(id_to_row[pid]) for pid in ranked]


@app.get("/library/notifications/{primary_id}", response_model=NotificationOut)
def get_notification(primary_id: int, db: Session = Depends(get_database)):
    row = db.query(Notification).filter(Notification.primary_id == primary_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationOut.model_validate(row)


@app.get("/library/circulars/{primary_id}", response_model=CircularOut)
def get_circular(primary_id: int, db: Session = Depends(get_database)):
    row = db.query(Circular).filter(Circular.primary_id == primary_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Circular not found")
    return CircularOut.model_validate(row)


@app.get("/library/notifications/{primary_id}/pdf")
def get_notification_pdf(primary_id: int, db: Session = Depends(get_database)):
    row = db.query(Notification).filter(Notification.primary_id == primary_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    pdf_path = _find_notif_pdf(NOTIF_PDF_DIR, row.category, row.year, row.notification_no)
    if not pdf_path or not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not available for this notification")
    return FileResponse(str(pdf_path), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})


@app.get("/library/circulars/{primary_id}/pdf")
def get_circular_pdf(primary_id: int, db: Session = Depends(get_database)):
    row = db.query(Circular).filter(Circular.primary_id == primary_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Circular not found")
    pdf_path = _find_circular_pdf(CIRCULAR_PDF_DIR, row.category, row.year, row.circular_no)
    if not pdf_path or not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not available for this circular")
    return FileResponse(str(pdf_path), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})


def _rrf_library(result_lists: list, limit: int, k: int = 60,
                 weights: list[float] | None = None) -> list[int]:
    """Reciprocal Rank Fusion across multiple ranked lists of (primary_id, score) rows."""
    scores: dict[int, float] = {}
    for lane, results in enumerate(result_lists):
        w = weights[lane] if weights and lane < len(weights) else 1.0
        for rank, row in enumerate(results):
            pid = row[0]
            scores[pid] = scores.get(pid, 0.0) + w / (k + rank + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)[:limit]


@app.get("/library/resolve/{doc_type}/{primary_id}")
def resolve_library_link(
    doc_type: str,
    primary_id: int,
    db: Session = Depends(get_database),
):
    """Generic resolver for frontend cross-reference link handler."""
    if doc_type not in {"act", "rule", "notification", "circular"}:
        raise HTTPException(
            status_code=400,
            detail="doc_type must be one of: act, rule, notification, circular",
        )
    if doc_type == "act":
        row = db.query(Act).filter(Act.primary_id == primary_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        out = ActOut.model_validate(row)
        out.cross_references = _resolve_xrefs("act", primary_id, db)
        return {"type": "act", "document": out.model_dump()}
    elif doc_type == "rule":
        row = db.query(Rule).filter(Rule.primary_id == primary_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        out = RuleOut.model_validate(row)
        out.cross_references = _resolve_xrefs("rule", primary_id, db)
        return {"type": "rule", "document": out.model_dump()}
    elif doc_type == "notification":
        row = db.query(Notification).filter(Notification.primary_id == primary_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        return {"type": "notification", "document": NotificationOut.model_validate(row).model_dump()}
    else:
        row = db.query(Circular).filter(Circular.primary_id == primary_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        return {"type": "circular", "document": CircularOut.model_validate(row).model_dump()}


@app.get("/library/search", response_model=list[LibrarySearchHit])
def library_search(
    q: str = Query(..., min_length=2),
    types: str = Query(None, description="Comma-separated subset: act,rule,notification,circular"),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_database),
):
    """Full-text search across Acts, Rules, Notifications, and Circulars."""
    all_types = {"act", "rule", "notification", "circular"}
    if types:
        enabled = {t.strip().lower() for t in types.split(",") if t.strip().lower() in all_types}
        if not enabled:
            enabled = all_types
    else:
        enabled = all_types

    per_type = max(1, limit // len(enabled))
    tsq = func.plainto_tsquery("english", q)
    hits: list[LibrarySearchHit] = []

    if "act" in enabled:
        rows = (
            db.query(Act)
            .filter(func.to_tsvector("english", func.coalesce(Act.content, "")).op("@@")(tsq))
            .limit(per_type)
            .all()
        )
        for r in rows:
            hits.append(LibrarySearchHit(
                type="act",
                primary_id=r.primary_id,
                label=r.section_no,
                title=f"{r.act_name} — {r.section_name or r.section_no}",
                snippet=(r.content or "")[:200] or None,
            ))

    if "rule" in enabled:
        rows = (
            db.query(Rule)
            .filter(func.to_tsvector("english", func.coalesce(Rule.content, "")).op("@@")(tsq))
            .limit(per_type)
            .all()
        )
        for r in rows:
            hits.append(LibrarySearchHit(
                type="rule",
                primary_id=r.primary_id,
                label=r.section_no,
                title=f"{r.act_name} — {r.section_name or r.section_no}",
                snippet=(r.content or "")[:200] or None,
            ))

    if "notification" in enabled:
        fts_doc = func.to_tsvector(
            "english",
            func.concat(func.coalesce(Notification.title, ""), " ", func.coalesce(Notification.content, "")),
        )
        rows = (
            db.query(Notification)
            .filter(fts_doc.op("@@")(tsq))
            .order_by(Notification.issued_on.desc().nulls_last())
            .limit(per_type)
            .all()
        )
        for r in rows:
            hits.append(LibrarySearchHit(
                type="notification",
                primary_id=r.primary_id,
                label=r.notification_no,
                title=r.title,
                snippet=(r.content or "")[:200] or None,
            ))

    if "circular" in enabled:
        fts_doc = func.to_tsvector(
            "english",
            func.concat(func.coalesce(Circular.subject, ""), " ", func.coalesce(Circular.content, "")),
        )
        rows = (
            db.query(Circular)
            .filter(fts_doc.op("@@")(tsq))
            .order_by(Circular.issued_on.desc().nulls_last())
            .limit(per_type)
            .all()
        )
        for r in rows:
            hits.append(LibrarySearchHit(
                type="circular",
                primary_id=r.primary_id,
                label=r.circular_no,
                title=r.subject,
                snippet=(r.content or "")[:200] or None,
            ))

    return hits
