"""Embed notifications and circulars for hybrid FTS + semantic search.

Two sequential steps — run in order, each is resumable:

  Step 1 (augment): OpenAI generates 3 questions + summary from existing content.
                    Writes embedding_text to notifications / circulars table.
  Step 2 (embed):   Chunk content, prepend embedding_text, embed, write to library_chunks.

Usage:
    set -a && source envs/.env.staging && set +a

    uv run python -m etl.embed_library_docs                     # both steps, both types
    uv run python -m etl.embed_library_docs --type notification
    uv run python -m etl.embed_library_docs --type circular
    uv run python -m etl.embed_library_docs --step augment      # LLM only
    uv run python -m etl.embed_library_docs --step embed        # embedding only
    uv run python -m etl.embed_library_docs --limit 10 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
DATABASE_URL     = os.getenv("DATABASE_URL", "")
EMBED_MODEL_NAME = os.getenv("MODEL_NAME", "sentence-transformers/all-mpnet-base-v2")

LLM_MODEL     = "gpt-4o-mini"
LLM_CHAR_CAP  = 12_000
CHUNK_SIZE    = 1_000
CHUNK_OVERLAP = 150

_SYSTEM = (
    "You are a GST law expert helping to annotate Indian GST notifications and circulars "
    "for a semantic search system. Your annotations must be concise and precise."
)


# ── DB ─────────────────────────────────────────────────────────────────────────

def _build_db_url() -> str:
    from urllib.parse import quote_plus
    if DATABASE_URL:
        return DATABASE_URL
    user     = os.getenv("POSTGRES_USER", "")
    password = os.getenv("POSTGRES_PASSWORD", "")
    host     = os.getenv("POSTGRES_HOST", "localhost")
    port     = os.getenv("POSTGRES_PORT", "5432")
    db       = os.getenv("POSTGRES_DB", "search_db")
    if not user:
        raise RuntimeError("Set DATABASE_URL or POSTGRES_USER/PASSWORD/DB/HOST env vars")
    return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}"


def _ensure_embedding_text_column(session: Session) -> None:
    session.execute(text(
        "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS embedding_text TEXT"
    ))
    session.execute(text(
        "ALTER TABLE circulars ADD COLUMN IF NOT EXISTS embedding_text TEXT"
    ))
    session.commit()


def _ensure_library_chunk_embedding_text_column(session: Session) -> None:
    session.execute(text(
        "ALTER TABLE library_chunks ADD COLUMN IF NOT EXISTS embedding_text TEXT"
    ))
    session.commit()


# ── Chunking ───────────────────────────────────────────────────────────────────

def chunk_text(txt: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not txt.strip():
        return []
    chunks, start = [], 0
    while start < len(txt):
        end = min(start + size, len(txt))
        chunk = txt[start:end]
        if end < len(txt):
            break_at = max(chunk.rfind(". "), chunk.rfind("\n"))
            if break_at > size - 200:
                end = start + break_at + 1
                chunk = txt[start:end]
        chunks.append(chunk.strip())
        if end >= len(txt):
            break
        start = end - overlap
    return [c for c in chunks if c]


# ── LLM prompt / parser ────────────────────────────────────────────────────────

def _build_prompt(doc_type: str, label: str, content: str) -> str:
    kind = "notification" if doc_type == "notification" else "circular"
    return (
        f"GST {kind}: {label}\n\n"
        f"Text (excerpt):\n{content[:LLM_CHAR_CAP]}\n\n"
        "Write exactly:\n"
        "- 3 plain-English questions a GST practitioner or business owner would ask "
        "that this document directly and specifically answers.\n"
        "- 1 plain-English sentence (≤25 words) summarising what this document does.\n\n"
        "Format your response exactly as:\n"
        "Q1: <question>\n"
        "Q2: <question>\n"
        "Q3: <question>\n"
        "Summary: <one sentence>"
    )


def _parse_response(text: str) -> tuple[str, str, str, str] | None:
    values: dict[str, str] = {}
    for line in [l.strip() for l in text.strip().splitlines() if l.strip()]:
        for key in ("Q1", "Q2", "Q3", "Summary"):
            if line.startswith(f"{key}:"):
                values[key] = line[len(key) + 1:].strip()
    if len(values) < 4:
        return None
    return values["Q1"], values["Q2"], values["Q3"], values["Summary"]


def _compose_prefix(q1: str, q2: str, q3: str, summary: str) -> str:
    return f"Q: {q1}\nQ: {q2}\nQ: {q3}\n{summary}"


# ── Step 1: LLM augmentation ───────────────────────────────────────────────────

def step_augment(session: Session, doc_type: str, client, limit: int | None,
                 dry_run: bool) -> None:
    table = "notifications" if doc_type == "notification" else "circulars"
    label_col = "notification_no" if doc_type == "notification" else "circular_no"

    rows = session.execute(text(
        f"SELECT primary_id, {label_col}, content FROM {table} "
        f"WHERE content IS NOT NULL AND length(content) > 50 "
        f"AND embedding_text IS NULL "
        f"ORDER BY year DESC, id"
        + (f" LIMIT {limit}" if limit else "")
    )).fetchall()

    log.info("[%s] augment: %d docs to process", doc_type, len(rows))
    if dry_run or not rows:
        return

    updated = parse_errors = 0
    for i, (primary_id, label, content) in enumerate(rows, 1):
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=LLM_MODEL,
                    max_tokens=256,
                    messages=[
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user",   "content": _build_prompt(doc_type, label, content)},
                    ],
                    temperature=0,
                )
                parsed = _parse_response(resp.choices[0].message.content)
                if parsed is None:
                    log.warning("Parse failure for %s %s", doc_type, label)
                    parse_errors += 1
                else:
                    q1, q2, q3, summary = parsed
                    session.execute(text(
                        f"UPDATE {table} SET embedding_text = :et WHERE primary_id = :pid"
                    ), {"et": _compose_prefix(q1, q2, q3, summary), "pid": primary_id})
                    updated += 1
                break
            except Exception as e:
                if attempt == 2:
                    log.warning("Failed %s %s after 3 attempts: %s", doc_type, label, e)
                else:
                    time.sleep(2 ** attempt)

        if i % 50 == 0:
            session.commit()
            log.info("  %s augment: %d / %d  (errors=%d)", doc_type, updated, len(rows), parse_errors)

    session.commit()
    log.info("[%s] augment done — updated=%d  parse_errors=%d", doc_type, updated, parse_errors)


# ── Step 2: Chunk (create LibraryChunk rows, no embeddings yet) ────────────────

def step_chunk(session: Session, doc_type: str, limit: int | None) -> None:
    """Chunk content from notifications/circulars into LibraryChunk rows (raw SQL).

    Uses raw SQL inserts in batches of 100 docs to keep peak memory low.
    embedding_text prefix is stored on the chunk row for use at embed time.
    """
    table = "notifications" if doc_type == "notification" else "circulars"

    rows = session.execute(text(
        f"SELECT t.primary_id, t.embedding_text, t.content FROM {table} t "
        f"WHERE t.embedding_text IS NOT NULL AND t.content IS NOT NULL "
        f"AND NOT EXISTS ("
        f"  SELECT 1 FROM library_chunks lc "
        f"  WHERE lc.doc_type = :dt AND lc.primary_id = t.primary_id"
        f") ORDER BY t.year DESC, t.id"
        + (f" LIMIT {limit}" if limit else "")
    ), {"dt": doc_type}).fetchall()

    log.info("[%s] chunk: %d docs to chunk", doc_type, len(rows))
    if not rows:
        return

    created = 0
    for batch_start in range(0, len(rows), 100):
        batch = rows[batch_start: batch_start + 100]
        for primary_id, embedding_text, content in batch:
            chunks = chunk_text(content)
            for i, chunk in enumerate(chunks):
                session.execute(text(
                    "INSERT INTO library_chunks "
                    "(doc_type, primary_id, chunk_index, content, embedding_text) "
                    "VALUES (:dt, :pid, :idx, :content, :et) "
                    "ON CONFLICT (doc_type, primary_id, chunk_index) DO NOTHING"
                ), {"dt": doc_type, "pid": primary_id, "idx": i,
                    "content": chunk, "et": embedding_text})
            created += len(chunks)
        session.commit()
        log.info("  [%s] chunk: %d / %d docs, %d chunks so far",
                 doc_type, min(batch_start + 100, len(rows)), len(rows), created)

    log.info("[%s] chunk done — %d chunks created", doc_type, created)


# ── Step 3: Embed (find unembedded LibraryChunk rows, encode, save) ────────────

def step_embed(session: Session, model_name: str, batch_size: int = 32) -> None:
    """Embed all LibraryChunk rows that have no embedding yet.

    Mirrors embed_chunks() in chunk_acts.py exactly.
    """
    try:
        from backend.app.embeddings import EmbeddingModel
        from backend.app.models import LibraryChunk
    except ImportError:
        from app.embeddings import EmbeddingModel  # type: ignore
        from app.models import LibraryChunk  # type: ignore

    log.info("Loading embedding model: %s", model_name)
    model = EmbeddingModel(model_name)

    rows = session.query(LibraryChunk).filter(LibraryChunk.embedding.is_(None)).all()
    if not rows:
        log.info("All library chunks already have embeddings.")
        return

    log.info("Embedding %d library chunks (batch_size=%d)…", len(rows), batch_size)
    updated = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        texts = [
            (lc.embedding_text + "\n\n" + lc.content) if lc.embedding_text else lc.content
            for lc in batch
        ]
        embeddings = model.encode(texts)
        for lc, emb in zip(batch, embeddings):
            lc.embedding = emb.tolist() if hasattr(emb, "tolist") else list(emb)
        session.commit()
        updated += len(batch)
        if updated % 500 == 0 or updated == len(rows):
            log.info("  %d / %d chunks embedded", updated, len(rows))

    log.info("Embedding complete — %d chunks updated.", updated)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Embed library docs for semantic search")
    parser.add_argument("--type",  choices=["notification", "circular", "both"], default="both")
    parser.add_argument("--step",  choices=["augment", "chunk", "embed", "all"], default="all",
                        help="augment=LLM questions+summary, chunk=create LibraryChunk rows, "
                             "embed=encode embeddings, all=run all three")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    types = ["notification", "circular"] if args.type == "both" else [args.type]

    db_url = _build_db_url()
    engine = create_engine(db_url)

    with Session(engine) as session:
        _ensure_embedding_text_column(session)
        _ensure_library_chunk_embedding_text_column(session)

        if args.step in ("augment", "all"):
            if not OPENAI_API_KEY:
                log.error("OPENAI_API_KEY not set")
                sys.exit(1)
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            for doc_type in types:
                step_augment(session, doc_type, client, args.limit, args.dry_run)

        if args.step in ("chunk", "all") and not args.dry_run:
            for doc_type in types:
                step_chunk(session, doc_type, args.limit)

        if args.step in ("embed", "all") and not args.dry_run:
            step_embed(session, EMBED_MODEL_NAME, args.batch_size)


if __name__ == "__main__":
    main()
