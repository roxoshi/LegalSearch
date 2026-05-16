"""
One-time LLM augmentation of act_chunks to improve vector search quality.

Problem: all-mpnet-base-v2 was not trained on legal text.
When users search with natural language ("when can ITC be reversed"), the model
can't bridge the gap to dense legislative prose.

Solution: prepend LLM-generated plain-English questions + a summary to each
sub-section before embedding. The enriched text is stored in act_chunks.embedding_text.
etl/chunk_acts.py already uses embedding_text when present.

Usage:
    # Step 1 — augment (writes embedding_text to DB):
    uv run python -m etl.augment_acts

    # Step 2 — re-embed with augmented text:
    uv run python -m etl.chunk_acts

    # Or do both in one go:
    uv run python -m etl.augment_acts --embed

    # Limit for testing:
    uv run python -m etl.augment_acts --limit 20 --embed
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from openai import OpenAI
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl.database import init_db

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

MODEL = "gpt-4o-mini"

# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a GST law expert helping to annotate Indian GST legislation "
    "for a semantic search system. Your annotations must be concise and precise."
)


def _build_prompt(act_name: str, section_no: str, section_name: str | None,
                  sub_section_label: str, chunk_content: str) -> str:
    section_title = f"{section_no}"
    if section_name:
        section_title += f" – {section_name}"

    return (
        f"Act: {act_name}\n"
        f"Section: {section_title}\n"
        f"Sub-section: {sub_section_label}\n\n"
        f"Legal text:\n{chunk_content}\n\n"
        "Write exactly:\n"
        "- 3 plain-English questions a GST practitioner or lawyer would ask "
        "that this sub-section directly and specifically answers.\n"
        "- 1 plain-English sentence (≤25 words) summarising what this "
        "sub-section says.\n\n"
        "Format your response exactly as:\n"
        "Q1: <question>\n"
        "Q2: <question>\n"
        "Q3: <question>\n"
        "Summary: <one sentence>"
    )


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(text: str) -> tuple[str, str, str, str] | None:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    values: dict[str, str] = {}
    for line in lines:
        for key in ("Q1", "Q2", "Q3", "Summary"):
            prefix = f"{key}:"
            if line.startswith(prefix):
                values[key] = line[len(prefix):].strip()
    if len(values) < 4:
        return None
    return values["Q1"], values["Q2"], values["Q3"], values["Summary"]


def _compose_embedding_text(q1: str, q2: str, q3: str, summary: str,
                             sub_section_label: str, chunk_content: str) -> str:
    return (
        f"Q: {q1}\n"
        f"Q: {q2}\n"
        f"Q: {q3}\n"
        f"{summary}\n\n"
        f"{sub_section_label}\n"
        f"{chunk_content}"
    )


# ── Sync API flow ─────────────────────────────────────────────────────────────

def _augment_sync(session: Session, chunks: list[tuple[ActChunk, Act]],
                  client: OpenAI) -> int:
    updated = 0
    parse_errors = 0

    for i, (chunk, act) in enumerate(chunks):
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    max_tokens=256,
                    messages=[
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": _build_prompt(
                            act.act_name,
                            act.section_no,
                            act.section_name,
                            chunk.sub_section_label,
                            chunk.chunk_content,
                        )},
                    ],
                    temperature=0,
                )
                parsed = _parse_response(resp.choices[0].message.content)
                if parsed is None:
                    log.warning("Chunk %d parse failure", chunk.id)
                    parse_errors += 1
                else:
                    q1, q2, q3, summary = parsed
                    chunk.embedding_text = _compose_embedding_text(
                        q1, q2, q3, summary, chunk.sub_section_label, chunk.chunk_content
                    )
                    updated += 1
                break
            except Exception as e:
                if attempt == 2:
                    log.warning("Chunk %d error after 3 attempts: %s", chunk.id, e)
                else:
                    time.sleep(2 ** attempt)

        if (i + 1) % 50 == 0:
            session.commit()
            log.info("  %d / %d committed", updated, len(chunks))

    session.commit()
    log.info("Done. Updated: %d  Parse errors: %d", updated, parse_errors)
    return updated


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Augment act_chunks with LLM-generated questions + summary."
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N chunks (useful for testing)")
    parser.add_argument("--embed", action="store_true",
                        help="Re-embed all act_chunks after augmentation")
    parser.add_argument("--model",
                        default=os.getenv("MODEL_NAME", "sentence-transformers/all-mpnet-base-v2"),
                        help="Embedding model for --embed step")
    parser.add_argument("--embed-batch-size", type=int, default=32)
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        log.error("OPENAI_API_KEY env var is not set.")
        sys.exit(1)

    SessionFactory = init_db()
    session: Session = SessionFactory()

    try:
        from sqlalchemy import text as sa_text
        session.execute(sa_text(
            "ALTER TABLE act_chunks ADD COLUMN IF NOT EXISTS embedding_text TEXT"
        ))
        session.commit()

        q = (
            session.query(ActChunk, Act)
            .join(Act, ActChunk.act_id == Act.id)
            .filter(ActChunk.embedding_text.is_(None))
            .filter(ActChunk.chunk_content.isnot(None))
            .order_by(ActChunk.id)
        )
        if args.limit:
            q = q.limit(args.limit)

        chunks: list[tuple[ActChunk, Act]] = q.all()

        if not chunks:
            log.info("All chunks already have embedding_text — nothing to do.")
        else:
            log.info("%d chunks need augmentation.", len(chunks))
            client = OpenAI(api_key=api_key)
            _augment_sync(session, chunks, client)

        if args.embed:
            log.info("Re-embedding all act_chunks…")
            from etl.chunk_acts import embed_chunks
            session.query(ActChunk).update({ActChunk.embedding: None})
            session.commit()
            embed_chunks(session, args.model, args.embed_batch_size)

    except Exception:
        session.rollback()
        log.exception("Augmentation failed — rolled back.")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
