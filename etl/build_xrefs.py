"""Build validity cross-references between circulars and notifications.

Parses subject/title text for CBIC's formulaic withdrawal/supersession language,
creates library_xrefs rows, and sets is_active=False on the target document.

Two passes per doc type:
  Forward  — the withdrawing doc's subject names its target:
             "Seeks to withdraw Circular No. 105/24/2019-GST"
  Reverse  — the withdrawn doc's own subject carries an annotation:
             "... Rescinded vide Circular No. 125/44/2019-GST"

Usage:
    set -a && source envs/.env.staging && set +a
    uv run python -m etl.build_xrefs              # both doc types, live run
    uv run python -m etl.build_xrefs --dry-run    # print matches, no DB writes
    uv run python -m etl.build_xrefs --type circular
    uv run python -m etl.build_xrefs --type notification
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── Regex patterns ──────────────────────────────────────────────────────────────
# Captures the target doc number from the withdrawing/superseding doc's text.

_NUM = r"([\d]+/[\w/\.\-\s\(\)]+?)"   # doc number: greedy-stop before "dated"

# Forward patterns (on the withdrawing/superseding document's subject/title)
_FWD_CIRCULAR = [
    # "Seeks to withdraw Circular No. 105/24/2019-GST"
    # "Withdrawal of Circular No. 106/25/2019-GST"
    # "seeks to ab-initio withdraw the Circular No. 107/26/2019"
    (re.compile(
        r"(?:withdrawal of|seeks to (?:ab[-\s]initio\s+)?withdraw(?:\s+the)?)"
        r"\s+[Cc]ircular\s+No\.?\s*" + _NUM,
        re.IGNORECASE,
    ), "WITHDRAWS"),
    # "Regarding withdrawal of circular No. 212/6/2024-GST"
    (re.compile(
        r"regarding\s+withdrawal\s+of\s+[Cc]ircular\s+No\.?\s*" + _NUM,
        re.IGNORECASE,
    ), "WITHDRAWS"),
]

_FWD_NOTIFICATION = [
    # "Seeks to rescind Notification No. 27/2022-Central Tax"
    # "Rescinds notification No. 45/2017-Union Territory Tax (Rate)"
    (re.compile(
        r"(?:seeks to\s+)?rescind[s]?\s+[Nn]otification\s+[Nn]o\.?\s*" + _NUM,
        re.IGNORECASE,
    ), "WITHDRAWS"),
    # "Seeks to supersede Notification No. 2/2017-Central Tax (Rate)"
    (re.compile(
        r"(?:seeks to\s+)?supersede[s]?\s+[Nn]otification\s+[Nn]o\.?\s*" + _NUM,
        re.IGNORECASE,
    ), "SUPERSEDES"),
]

# Reverse patterns (on the withdrawn document's own subject/title)
_REV_CIRCULAR = re.compile(
    r"[Rr]escinded\s+vide\s+[Cc]ircular\s+No\.?\s*" + _NUM,
    re.IGNORECASE,
)
_REV_NOTIFICATION = re.compile(
    r"[Rr]escinded\s+vide\s+[Nn]otification\s+[Nn]o\.?\s*" + _NUM,
    re.IGNORECASE,
)

# "ab-initio" signal — withdrawer's text
_ABINIT = re.compile(r"\bab[-\s]initio\b", re.IGNORECASE)


def _normalise(raw: str) -> str:
    """Strip trailing noise ('dated ...', extra spaces/hyphens) from an extracted number."""
    # Drop " dated DD.MM.YYYY" and everything after
    raw = re.sub(r"\s+dated\b.*", "", raw, flags=re.IGNORECASE)
    # Collapse internal whitespace and normalise dashes
    raw = re.sub(r"\s*-\s*", "-", raw.strip())
    raw = re.sub(r"\s+", " ", raw).strip().rstrip(".")
    return raw


def _find_by_no(session: Session, table: str, no_col: str, raw_no: str) -> int | None:
    """Look up a document's primary_id by a fuzzy match on its number column."""
    norm = _normalise(raw_no)
    row = session.execute(text(
        f"SELECT primary_id FROM {table} WHERE {no_col} ILIKE :p LIMIT 1"
    ), {"p": f"%{norm}%"}).fetchone()
    if row:
        return row[0]
    # Fallback: try just the leading digits (e.g., "105")
    lead = re.match(r"^([\d]+)", norm)
    if lead:
        rows = session.execute(text(
            f"SELECT primary_id, {no_col} FROM {table} "
            f"WHERE {no_col} ILIKE :p ORDER BY {no_col} LIMIT 5"
        ), {"p": f"{lead.group(1)}/%"}).fetchall()
        if len(rows) == 1:
            return rows[0][0]
    return None


def _upsert_xref(session: Session, from_type: str, from_id: int,
                 to_type: str, to_id: int, rel: str, ab_initio: bool,
                 dry_run: bool) -> bool:
    if dry_run:
        return True
    session.execute(text("""
        INSERT INTO library_xrefs
            (from_doc_type, from_primary_id, to_doc_type, to_primary_id,
             relationship, ab_initio)
        VALUES
            (:fdt, :fid, :tdt, :tid, CAST(:rel AS xrefrelationship), :ai)
        ON CONFLICT (from_doc_type, from_primary_id, to_doc_type, to_primary_id)
        DO UPDATE SET relationship = EXCLUDED.relationship,
                      ab_initio    = EXCLUDED.ab_initio
    """), {"fdt": from_type, "fid": from_id, "tdt": to_type,
           "tid": to_id, "rel": rel, "ai": ab_initio})
    return True


def _deactivate(session: Session, table: str, primary_id: int, dry_run: bool) -> None:
    if dry_run:
        return
    session.execute(text(
        f"UPDATE {table} SET is_active = FALSE WHERE primary_id = :pid"
    ), {"pid": primary_id})


# ── Per-doc-type processing ─────────────────────────────────────────────────────

def _process(session: Session, doc_type: str, dry_run: bool) -> dict:
    is_circular = (doc_type == "circular")
    table    = "circulars"      if is_circular else "notifications"
    no_col   = "circular_no"   if is_circular else "notification_no"
    txt_col  = "subject"       if is_circular else "title"
    fwd_pats = _FWD_CIRCULAR   if is_circular else _FWD_NOTIFICATION
    rev_pat  = _REV_CIRCULAR   if is_circular else _REV_NOTIFICATION

    rows = session.execute(text(
        f"SELECT primary_id, {no_col}, {txt_col} FROM {table} WHERE {txt_col} IS NOT NULL"
    )).fetchall()

    stats = {"forward": 0, "reverse": 0, "unresolved": 0}

    for primary_id, doc_no, text_val in rows:
        # ── Forward pass ─────────────────────────────────────────────
        for pat, rel in fwd_pats:
            m = pat.search(text_val)
            if not m:
                continue
            raw_target = m.group(1)
            ab_initio  = bool(_ABINIT.search(text_val))
            target_id  = _find_by_no(session, table, no_col, raw_target)
            if target_id is None:
                log.warning("[%s] %s: forward match '%s' → target not found",
                            doc_type, doc_no, _normalise(raw_target))
                stats["unresolved"] += 1
                continue
            log.info("[%s] FORWARD %s: %s %s → primary_id=%d (ab_initio=%s)",
                     doc_type, rel, doc_no, _normalise(raw_target), target_id, ab_initio)
            _upsert_xref(session, doc_type, primary_id,
                         doc_type, target_id, rel, ab_initio, dry_run)
            if rel in ("WITHDRAWS", "SUPERSEDES"):
                _deactivate(session, table, target_id, dry_run)
            stats["forward"] += 1
            break  # one forward match per doc is enough

        # ── Reverse pass ─────────────────────────────────────────────
        m = rev_pat.search(text_val)
        if m:
            raw_withdrawer = m.group(1)
            withdrawer_id  = _find_by_no(session, table, no_col, raw_withdrawer)
            if withdrawer_id is None:
                log.warning("[%s] %s: reverse match '%s' → withdrawer not found",
                            doc_type, doc_no, _normalise(raw_withdrawer))
                stats["unresolved"] += 1
            else:
                log.info("[%s] REVERSE WITHDRAWS %s ← %s (primary_id=%d)",
                         doc_type, doc_no, _normalise(raw_withdrawer), withdrawer_id)
                _upsert_xref(session, doc_type, withdrawer_id,
                             doc_type, primary_id, "WITHDRAWS", False, dry_run)
                _deactivate(session, table, primary_id, dry_run)
                stats["reverse"] += 1

    return stats


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _build_db_url() -> str:
    from urllib.parse import quote_plus
    if url := os.getenv("DATABASE_URL", ""):
        return url
    user = os.getenv("POSTGRES_USER", "")
    if not user:
        raise RuntimeError("Set DATABASE_URL or POSTGRES_USER/PASSWORD/DB/HOST env vars")
    return (f"postgresql://{quote_plus(user)}:{quote_plus(os.getenv('POSTGRES_PASSWORD',''))}"
            f"@{os.getenv('POSTGRES_HOST','localhost')}:{os.getenv('POSTGRES_PORT','5432')}"
            f"/{os.getenv('POSTGRES_DB','search_db')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build library_xrefs from subject/title text")
    parser.add_argument("--type", choices=["circular", "notification", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print matches without writing to DB")
    args = parser.parse_args()

    types = ["circular", "notification"] if args.type == "both" else [args.type]

    engine = create_engine(_build_db_url())
    with Session(engine) as session:
        for doc_type in types:
            log.info("Processing %ss%s …", doc_type, " (DRY RUN)" if args.dry_run else "")
            stats = _process(session, doc_type, args.dry_run)
            if not args.dry_run:
                session.commit()
            log.info("[%s] done — forward=%d  reverse=%d  unresolved=%d",
                     doc_type, stats["forward"], stats["reverse"], stats["unresolved"])


if __name__ == "__main__":
    main()
