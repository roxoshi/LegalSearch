"""
Split Act sections into sub-section chunks and generate embeddings.

Each section in the acts table is split at top-level numbered sub-section
boundaries — (1), (2), (3) etc. — so that searches hit a precise sub-section
rather than the full section blob.

The preamble (text before the first numbered sub-section) is kept as its own
chunk labelled with just the section number e.g. "Section 17".

Idempotent: deletes existing chunks for each act before re-creating them,
so safe to re-run after content updates.

Usage:
    uv run python -m etl.chunk_acts                          # embed all
    uv run python -m etl.chunk_acts --no-embed               # chunk only, no embeddings
    uv run python -m etl.chunk_acts --model <model_name>
"""

import argparse
import logging
import os
import re
import sys

from sqlalchemy.orm import Session

from .database import init_db

try:
    from backend.app.models import Act, ActChunk
except ImportError:
    from app.models import Act, ActChunk  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Matches top-level numbered sub-sections: (1), (2), (10) etc.
# Must appear after a newline with optional whitespace.
_SUBSECTION_RE = re.compile(r'\n\s*(\(\d+\))\s')


def split_section(section_no: str, content: str) -> list[tuple[str, str]]:
    """Split a section's content at top-level (digit) sub-section boundaries.

    Returns a list of (label, text) tuples:
      - Preamble gets label = section_no  e.g. "Section 17"
      - Sub-sections get label = section_no + marker  e.g. "Section 17(5)"
    """
    parts = _SUBSECTION_RE.split(content)
    # After split with one capturing group:
    # parts = [preamble, "(1)", text1, "(2)", text2, ...]

    chunks: list[tuple[str, str]] = []

    preamble = parts[0].strip()
    if preamble:
        chunks.append((section_no, preamble))

    i = 1
    while i < len(parts) - 1:
        marker = parts[i]           # e.g. "(5)"
        text = parts[i + 1].strip()
        label = f"{section_no}{marker}"
        if text:
            # Prepend the marker so the chunk is self-contained
            chunks.append((label, f"{marker} {text}"))
        i += 2

    return chunks


def chunk_acts(session: Session) -> int:
    """Delete and re-create ActChunk rows for all acts. Returns total chunks created."""
    acts = session.query(Act).order_by(Act.id).all()
    if not acts:
        log.warning("No acts found in DB — run ingest_legal_library first.")
        return 0

    total = 0
    for act in acts:
        # Clear existing chunks for this section
        session.query(ActChunk).filter(ActChunk.act_id == act.id).delete()

        content = act.content or ""
        if not content.strip():
            continue

        chunks = split_section(act.section_no, content)
        for label, text in chunks:
            session.add(ActChunk(
                act_id=act.id,
                sub_section_label=label,
                chunk_content=text,
            ))
            total += 1

    session.commit()
    log.info("Created %d chunks from %d sections.", total, len(acts))
    return total


def embed_chunks(session: Session, model_name: str, batch_size: int = 32) -> int:
    """Generate embeddings for ActChunk rows that have none yet.

    Embedding text priority (highest to lowest):
      1. chunk.embedding_text  — set by augment_acts.py (chunk-level Claude annotations)
      2. parent Act q1/q2/q3  — set by annotate_acts.py --load-db (section-level Gemini annotations)
      3. label + raw content   — fallback
    """
    try:
        from backend.app.embeddings import EmbeddingModel
        from backend.app.models import Act
    except ImportError:
        from app.embeddings import EmbeddingModel  # type: ignore
        from app.models import Act                  # type: ignore

    log.info("Loading embedding model: %s", model_name)
    model = EmbeddingModel(model_name)

    rows = (
        session.query(ActChunk, Act)
        .join(Act, ActChunk.act_id == Act.id)
        .filter(ActChunk.embedding.is_(None))
        .all()
    )
    if not rows:
        log.info("All chunks already have embeddings.")
        return 0

    log.info("Embedding %d chunks (batch_size=%d)…", len(rows), batch_size)
    updated = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        texts = []
        for chunk, act in batch:
            if getattr(chunk, "embedding_text", None):
                # Chunk-level annotation from augment_acts.py (most precise)
                texts.append(chunk.embedding_text)
            elif act.q1:
                # Section-level annotation from annotate_acts.py
                texts.append(
                    f"Q: {act.q1}\n"
                    f"Q: {act.q2}\n"
                    f"Q: {act.q3}\n"
                    f"{act.ann_summary}\n\n"
                    f"{chunk.sub_section_label}\n{chunk.chunk_content}"
                )
            else:
                texts.append(f"{chunk.sub_section_label}\n{chunk.chunk_content}")

        embeddings = model.encode(texts)
        for (chunk, _act), emb in zip(batch, embeddings):
            chunk.embedding = emb.tolist() if hasattr(emb, "tolist") else list(emb)
        session.commit()
        updated += len(batch)
        log.info("  %d / %d", updated, len(rows))

    log.info("Embedding complete — %d chunks updated.", updated)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk act sections into sub-section embeddings")
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding generation (chunk only)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"),
        help="Embedding model name",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Embedding batch size (default: 32)",
    )
    args = parser.parse_args()

    SessionFactory = init_db()
    session = SessionFactory()

    try:
        total_chunks = chunk_acts(session)
        if not args.no_embed and total_chunks > 0:
            embed_chunks(session, args.model, args.batch_size)
    except Exception:
        session.rollback()
        log.exception("Failed — rolled back.")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
