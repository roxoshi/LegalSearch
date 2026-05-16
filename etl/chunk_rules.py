"""
Split Rule sections into sub-section chunks and generate embeddings.

Mirrors etl/chunk_acts.py but operates on the rules/rule_chunks tables.

Usage:
    uv run python -m etl.chunk_rules                     # chunk + embed all
    uv run python -m etl.chunk_rules --no-embed          # chunk only
    uv run python -m etl.chunk_rules --model <name>
"""

import argparse
import logging
import os
import re
import sys

from sqlalchemy.orm import Session

from .database import init_db

try:
    from backend.app.models import Rule, RuleChunk
except ImportError:
    from app.models import Rule, RuleChunk  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_SUBSECTION_RE = re.compile(r'\n\s*(\(\d+\))\s')


def split_section(section_no: str, content: str) -> list[tuple[str, str]]:
    parts = _SUBSECTION_RE.split(content)
    chunks: list[tuple[str, str]] = []

    preamble = parts[0].strip()
    if preamble:
        chunks.append((section_no, preamble))

    i = 1
    while i < len(parts) - 1:
        marker = parts[i]
        text = parts[i + 1].strip()
        label = f"{section_no}{marker}"
        if text:
            chunks.append((label, f"{marker} {text}"))
        i += 2

    return chunks


def chunk_rules(session: Session) -> int:
    rules = session.query(Rule).order_by(Rule.id).all()
    if not rules:
        log.warning("No rules found in DB — run ingest first.")
        return 0

    total = 0
    for rule in rules:
        session.query(RuleChunk).filter(RuleChunk.rule_id == rule.id).delete()

        content = rule.content or ""
        if not content.strip():
            continue

        chunks = split_section(rule.section_no, content)
        for label, text in chunks:
            session.add(RuleChunk(
                rule_id=rule.id,
                sub_section_label=label,
                chunk_content=text,
            ))
            total += 1

    session.commit()
    log.info("Created %d chunks from %d rules.", total, len(rules))
    return total


def embed_chunks(session: Session, model_name: str, batch_size: int = 32) -> int:
    try:
        from backend.app.embeddings import EmbeddingModel
    except ImportError:
        from app.embeddings import EmbeddingModel  # type: ignore

    log.info("Loading embedding model: %s", model_name)
    model = EmbeddingModel(model_name)

    rows = (
        session.query(RuleChunk, Rule)
        .join(Rule, RuleChunk.rule_id == Rule.id)
        .filter(RuleChunk.embedding.is_(None))
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
        for chunk, rule in batch:
            if getattr(chunk, "embedding_text", None):
                texts.append(chunk.embedding_text)
            elif rule.q1:
                texts.append(
                    f"Q: {rule.q1}\n"
                    f"Q: {rule.q2}\n"
                    f"Q: {rule.q3}\n"
                    f"{rule.ann_summary}\n\n"
                    f"{chunk.sub_section_label}\n{chunk.chunk_content}"
                )
            else:
                texts.append(f"{chunk.sub_section_label}\n{chunk.chunk_content}")

        embeddings = model.encode(texts)
        for (chunk, _rule), emb in zip(batch, embeddings):
            chunk.embedding = emb.tolist() if hasattr(emb, "tolist") else list(emb)
        session.commit()
        updated += len(batch)
        log.info("  %d / %d", updated, len(rows))

    log.info("Embedding complete — %d chunks updated.", updated)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk rule sections into sub-section embeddings")
    parser.add_argument("--no-embed", action="store_true", help="Skip embedding generation")
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL_NAME", "sentence-transformers/all-mpnet-base-v2"),
        help="Embedding model name",
    )
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding batch size")
    args = parser.parse_args()

    SessionFactory = init_db()
    session = SessionFactory()

    try:
        total_chunks = chunk_rules(session)
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
