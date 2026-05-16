"""
Ingest Acts, Rules, Notifications, and Circulars from .data/ JSON files
into PostgreSQL. Also parses cross-reference hyperlinks from html_content
and populates the cross_references table.

Idempotent: safe to re-run; uses ON CONFLICT DO UPDATE (upsert) on content_id
for the four document tables and ON CONFLICT DO NOTHING for cross_references.

Usage:
    python -m etl.ingest_legal_library                        # default: .data/
    python -m etl.ingest_legal_library --data-dir /path/to/data
    python -m etl.ingest_legal_library --dry-run              # parse only, no DB writes
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .database import get_db_url, init_db

try:
    from backend.app.embeddings import EmbeddingModel
except ImportError:
    from app.embeddings import EmbeddingModel  # type: ignore

try:
    from backend.app.models import Act, Circular, CrossReference, DocType, Notification, Rule
except ImportError:
    from app.models import Act, Circular, CrossReference, DocType, Notification, Rule  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Matches href patterns like:
#   .../explore-notification/1000868
#   .../explore-rule/1000080
_XREF_RE = re.compile(r"explore-(notification|rule|act|circular)/(\d+)", re.IGNORECASE)
# Minimal <a> tag extractor for anchor text
_ANCHOR_RE = re.compile(r'<a\s[^>]*href=["\'][^"\']*explore-(?:notification|rule|act|circular)/\d+[^"\']*["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean(value: str | None) -> str | None:
    """Strip NUL bytes that PostgreSQL text fields reject."""
    if value is None:
        return None
    return value.replace("\x00", "")


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string; return None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _load_json(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a JSON array")
    return data


def _strip_tags(html: str) -> str:
    """Very lightweight tag stripper for anchor text cleanup."""
    return re.sub(r"<[^>]+>", "", html).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Ingestors
# ─────────────────────────────────────────────────────────────────────────────

_ACTS_SKIP = {"annotations", "batch_input"}

def ingest_acts(session: Session, data_dir: Path, dry_run: bool) -> int:
    """Load all JSON files under data_dir/acts/ into the acts table."""
    acts_dir = data_dir / "acts"
    files = [p for p in sorted(acts_dir.glob("*.json"))
             if p.stem not in _ACTS_SKIP and not p.stem.startswith("batch_")]
    if not files:
        log.warning("No JSON files found in %s", acts_dir)
        return 0

    total = 0
    for path in files:
        records = _load_json(path)
        log.info("acts  ← %s  (%d records)", path.name, len(records))
        for rec in records:
            row = {
                "primary_id":   rec["primary_id"],
                "content_id":   rec["content_id"],
                "act_name":     _clean(rec.get("act_name")),
                "chapter_no":   _clean(rec.get("chapter_no")),
                "chapter_name": _clean(rec.get("chapter_name")),
                "section_no":   _clean(rec.get("section_no", "")),
                "section_name": _clean(rec.get("section_name")),
                "content":      _clean(rec.get("content")),
                "html_content": _clean(rec.get("html")),
                "source_url":   _clean(rec.get("source_url")),
            }
            if not dry_run:
                stmt = (
                    pg_insert(Act)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["content_id"],
                        set_={k: v for k, v in row.items() if k != "content_id"},
                    )
                )
                session.execute(stmt)
            total += 1
        if not dry_run:
            session.commit()

    return total


def ingest_rules(session: Session, data_dir: Path, dry_run: bool) -> int:
    """Load all JSON files under data_dir/rules/ into the rules table."""
    rules_dir = data_dir / "rules"
    files = [p for p in sorted(rules_dir.glob("*.json")) if p.stem != "annotations"]
    if not files:
        log.warning("No JSON files found in %s", rules_dir)
        return 0

    total = 0
    for path in files:
        records = _load_json(path)
        log.info("rules ← %s  (%d records)", path.name, len(records))
        for rec in records:
            row = {
                "primary_id":   rec["primary_id"],
                "content_id":   rec["content_id"],
                "act_name":     _clean(rec.get("act_name")),
                "chapter_id":   rec.get("chapter_id"),
                "section_no":   _clean(rec.get("section_no", "")),
                "section_name": _clean(rec.get("section_name")),
                "content":      _clean(rec.get("content")),
                "html_content": _clean(rec.get("html")),
                "source_url":   _clean(rec.get("source_url")),
            }
            if not dry_run:
                stmt = (
                    pg_insert(Rule)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["content_id"],
                        set_={k: v for k, v in row.items() if k != "content_id"},
                    )
                )
                session.execute(stmt)
            total += 1
        if not dry_run:
            session.commit()

    return total


def ingest_notifications(session: Session, data_dir: Path, dry_run: bool) -> int:
    """Load all JSON files under data_dir/notifications/jsons/ into the notifications table."""
    notif_dir = data_dir / "notifications" / "jsons"
    files = sorted(notif_dir.glob("*.json"))
    if not files:
        log.warning("No JSON files found in %s", notif_dir)
        return 0

    total = 0
    for path in files:
        records = _load_json(path)
        log.info("notif ← %s  (%d records)", path.name, len(records))
        for rec in records:
            if not rec.get("primary_id") or not rec.get("content_id"):
                log.debug("Skipping notification with missing primary_id/content_id")
                continue
            meta: dict[str, Any] = rec.get("meta") or {}
            row = {
                "primary_id":      rec["primary_id"],
                "content_id":      rec["content_id"],
                "notification_no": _clean(rec.get("notification_no") or ""),
                "issued_on":       _parse_dt(rec.get("date")),
                "title":           _clean(rec.get("title")),
                "content":         _clean(rec.get("content")),
                "category":        meta.get("category"),
                "year":            meta.get("year"),
                "doc_path":        meta.get("doc_path"),
                "order_id":        meta.get("order_id"),
                "is_active":       bool(rec.get("is_active", True)),
                "is_amended":      bool(rec.get("is_amended", False)),
            }
            if not dry_run:
                stmt = (
                    pg_insert(Notification)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["content_id"],
                        set_={k: v for k, v in row.items() if k != "content_id"},
                    )
                )
                session.execute(stmt)
            total += 1
        if not dry_run:
            session.commit()

    return total


def ingest_circulars(session: Session, data_dir: Path, dry_run: bool) -> int:
    """Load all JSON files under data_dir/circulars/jsons/ into the circulars table."""
    circ_dir = data_dir / "circulars" / "jsons"
    files = sorted(circ_dir.glob("*.json"))
    if not files:
        log.warning("No JSON files found in %s", circ_dir)
        return 0

    total = 0
    for path in files:
        records = _load_json(path)
        log.info("circ  ← %s  (%d records)", path.name, len(records))
        for rec in records:
            meta: dict[str, Any] = rec.get("meta") or {}
            row = {
                "primary_id": rec["primary_id"],
                "content_id": rec["content_id"],
                "circular_no": rec.get("circular_no", ""),
                "issued_on":  _parse_dt(rec.get("date")),
                "subject":    rec.get("subject"),
                "content":    rec.get("content"),
                "category":   meta.get("category"),
                "year":       meta.get("year"),
                "doc_path":   meta.get("doc_path"),
                "order_id":   meta.get("order_id"),
                "is_active":  bool(rec.get("is_active", True)),
                "is_amended": bool(rec.get("is_amended", False)),
            }
            if not dry_run:
                stmt = (
                    pg_insert(Circular)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["content_id"],
                        set_={k: v for k, v in row.items() if k != "content_id"},
                    )
                )
                session.execute(stmt)
            total += 1
        if not dry_run:
            session.commit()

    return total


def ingest_cross_references(session: Session, data_dir: Path, dry_run: bool) -> int:
    """
    Parse hyperlinks from html_content of Acts and Rules, then populate
    the cross_references table.

    Links follow the pattern:  explore-{target_type}/{target_primary_id}
    Each unique (source_type, source_id, target_type, target_id) is one row.
    Duplicate links within the same section are deduplicated before insert.
    """
    _TYPE_MAP = {
        "act":          DocType.act,
        "rule":         DocType.rule,
        "notification": DocType.notification,
        "circular":     DocType.circular,
    }

    def _extract_xrefs(
        html: str,
        source_type: DocType,
        source_primary_id: int,
    ) -> list[dict]:
        """Return list of cross-reference dicts parsed from a single html string."""
        seen: set[tuple] = set()
        xrefs = []

        # Build a map of href-snippet → anchor_text using the anchor regex
        anchor_map: dict[str, str] = {}
        for m in _ANCHOR_RE.finditer(html):
            key_match = _XREF_RE.search(m.group(0))
            if key_match:
                anchor_map[f"{key_match.group(1)}/{key_match.group(2)}"] = (
                    _strip_tags(m.group(1))
                )

        for m in _XREF_RE.finditer(html):
            raw_type = m.group(1).lower()
            target_id = int(m.group(2))
            target_type = _TYPE_MAP.get(raw_type)
            if target_type is None:
                continue

            key = (source_type, source_primary_id, target_type, target_id)
            if key in seen:
                continue
            seen.add(key)

            xrefs.append({
                "source_type": source_type,
                "source_id":   source_primary_id,
                "target_type": target_type,
                "target_id":   target_id,
                "anchor_text": anchor_map.get(f"{raw_type}/{target_id}"),
            })
        return xrefs

    total = 0

    # Acts
    for path in [p for p in sorted((data_dir / "acts").glob("*.json"))
                 if p.stem not in _ACTS_SKIP and not p.stem.startswith("batch_")]:
        for rec in _load_json(path):
            html = rec.get("html") or ""
            if not html:
                continue
            for xref in _extract_xrefs(html, DocType.act, rec["primary_id"]):
                if not dry_run:
                    stmt = (
                        pg_insert(CrossReference)
                        .values(**xref)
                        .on_conflict_do_nothing(
                            constraint="uq_cross_reference"
                        )
                    )
                    session.execute(stmt)
                total += 1
        if not dry_run:
            session.commit()
        log.info("xrefs ← acts/%s  (+%d so far)", path.name, total)

    # Rules
    for path in [p for p in sorted((data_dir / "rules").glob("*.json")) if p.stem != "annotations"]:
        before = total
        for rec in _load_json(path):
            html = rec.get("html") or ""
            if not html:
                continue
            for xref in _extract_xrefs(html, DocType.rule, rec["primary_id"]):
                if not dry_run:
                    stmt = (
                        pg_insert(CrossReference)
                        .values(**xref)
                        .on_conflict_do_nothing(
                            constraint="uq_cross_reference"
                        )
                    )
                    session.execute(stmt)
                total += 1
        if not dry_run:
            session.commit()
        log.info("xrefs ← rules/%s  (+%d links)", path.name, total - before)

    return total


# ─────────────────────────────────────────────────────────────────────────────
# Embedding
# ─────────────────────────────────────────────────────────────────────────────

def embed_acts(session: Session, model_name: str, batch_size: int = 32) -> int:
    """Generate and store embeddings for all Act rows that have no embedding yet.

    The text embedded per section is:
        "{section_no}. {section_name}\n{content}"

    Only rows with embedding IS NULL are processed, so this is safe to re-run
    after partial runs or after new sections are ingested.

    Returns the number of rows updated.
    """
    log.info("Loading embedding model: %s", model_name)
    model = EmbeddingModel(model_name)

    # Fetch rows that still need embeddings
    rows = session.query(Act).filter(Act.embedding.is_(None)).all()
    if not rows:
        log.info("All act sections already have embeddings — nothing to do.")
        return 0

    log.info("Generating embeddings for %d act sections (batch_size=%d)…", len(rows), batch_size)
    updated = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [
            f"{r.section_no}. {r.section_name or ''}\n{r.content or ''}".strip()
            for r in batch
        ]
        embeddings = model.encode(texts)
        for row, emb in zip(batch, embeddings):
            row.embedding = emb.tolist() if hasattr(emb, "tolist") else list(emb)
        session.commit()
        updated += len(batch)
        log.info("  embedded %d / %d", updated, len(rows))

    log.info("Embedding complete — %d rows updated.", updated)
    return updated


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest legal library JSON files into PostgreSQL")
    parser.add_argument(
        "--data-dir",
        default=".data",
        help="Root directory containing acts/, rules/, notifications/, circulars/ (default: .data)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and count records without writing to the database",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="After ingestion, generate vector embeddings for act sections (requires transformers)",
    )
    parser.add_argument(
        "--model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="Embedding model name (default: sentence-transformers/all-MiniLM-L6-v2)",
    )
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=32,
        help="Batch size for embedding generation (default: 32)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        log.error("Data directory not found: %s", data_dir)
        sys.exit(1)

    log.info("Data directory : %s", data_dir)
    log.info("Dry run        : %s", args.dry_run)

    if args.dry_run:
        session = None  # type: ignore
    else:
        SessionFactory = init_db()
        session = SessionFactory()

    try:
        counts: dict[str, int] = {}

        counts["acts"]             = ingest_acts(session, data_dir, args.dry_run)
        counts["rules"]            = ingest_rules(session, data_dir, args.dry_run)
        counts["notifications"]    = ingest_notifications(session, data_dir, args.dry_run)
        counts["circulars"]        = ingest_circulars(session, data_dir, args.dry_run)
        counts["cross_references"] = ingest_cross_references(session, data_dir, args.dry_run)

        if args.embed and not args.dry_run:
            counts["embeddings"] = embed_acts(session, args.model, args.embed_batch_size)

        log.info("─" * 50)
        log.info("Ingestion complete%s:", " (dry run)" if args.dry_run else "")
        for name, count in counts.items():
            log.info("  %-20s %d", name, count)
        log.info("  %-20s %d", "TOTAL docs", sum(v for k, v in counts.items() if k not in ("cross_references", "embeddings")))

    except Exception:
        if session:
            session.rollback()
        log.exception("Ingestion failed — rolled back")
        sys.exit(1)
    finally:
        if session:
            session.close()


if __name__ == "__main__":
    main()
