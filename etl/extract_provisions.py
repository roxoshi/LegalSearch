"""
Extract primary provisions (sections/rules/articles) from analysis JSONs.

Two-pass strategy:
  Pass 1 — regex on primary fields: issues + ratio_decidendi + conclusion
  Pass 2 — regex on all 10 fields, require frequency >= 2 (appears in 2+ fields)

Reports how many docs are still null after both passes so we can decide
whether an LLM pass is worth it.
"""

import json
import logging
import os
import re
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl.database import get_db_url, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ── Regex ──────────────────────────────────────────────────────────────────────
# Captures: Section 16 / Section 16(2) / Section 16(2)(c) / Section 16(2)(aa)
#           Rule 89 / Rule 89(4) / Article 226 / Schedule III
_PROVISION_RE = re.compile(
    r"\b(Section\s+\d+[A-Za-z]?(?:\(\w+\))*"
    r"|Rule\s+\d+[A-Za-z]?(?:\(\w+\))*"
    r"|Article\s+\d+[A-Za-z]?(?:\(\w+\))*"
    r"|Schedule\s+(?:[IVX]+|\d+)(?:\s+Entry\s+\d+)?)",
    re.IGNORECASE,
)

PRIMARY_FIELDS = ("issues", "ratio_decidendi", "conclusion")
ALL_FIELDS = (
    "summary", "facts", "issues", "petitioner_arguments", "respondent_arguments",
    "analysis_of_law", "precedent_analysis", "courts_reasoning", "conclusion",
    "ratio_decidendi",
)


def _normalise(match: str) -> str:
    """Capitalise first word, strip extra whitespace."""
    parts = match.strip().split(None, 1)
    return parts[0].capitalize() + " " + parts[1] if len(parts) == 2 else match.strip()


def extract_from_text(text: str) -> list[str]:
    return [_normalise(m) for m in _PROVISION_RE.findall(text)]


def pass1(data: dict) -> list[str]:
    """Extract from primary fields only."""
    found = []
    for field in PRIMARY_FIELDS:
        found.extend(extract_from_text(data.get(field, "") or ""))
    # Deduplicate preserving order
    seen = set()
    return [p for p in found if not (p in seen or seen.add(p))]


def pass2(data: dict) -> list[str]:
    """Extract from all 10 fields; any mention counts."""
    found = []
    for field in ALL_FIELDS:
        found.extend(extract_from_text(data.get(field, "") or ""))
    seen = set()
    return [p for p in found if not (p in seen or seen.add(p))]


def main():
    analysis_dir = Path(os.getenv("ANALYSIS_DIR", ".data/batch_analysis/successes"))
    if not analysis_dir.exists():
        logger.error(f"Analysis dir not found: {analysis_dir}")
        sys.exit(1)

    SessionLocal = init_db()

    # Add column if missing
    with SessionLocal() as db:
        db.execute(text("""
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS primary_provisions text[];
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_documents_primary_provisions
            ON documents USING gin(primary_provisions);
        """))
        db.commit()
        logger.info("Column and index ready.")

    json_files = sorted(analysis_dir.rglob("*.json"))
    logger.info(f"Processing {len(json_files)} analysis files.")

    stats = {"pass1": 0, "pass2": 0, "null": 0, "not_in_db": 0}

    with SessionLocal() as db:
        for json_file in json_files:
            case_id = json_file.stem
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"Could not read {json_file.name}: {e}")
                continue

            # Pass 1
            provisions = pass1(data)
            source = "pass1"

            # Pass 2 fallback
            if not provisions:
                provisions = pass2(data)
                source = "pass2"

            if not provisions:
                source = "null"

            result = db.execute(
                text("SELECT id FROM documents WHERE case_id = :cid"),
                {"cid": case_id},
            ).first()

            if result is None:
                stats["not_in_db"] += 1
                continue

            db.execute(
                text("UPDATE documents SET primary_provisions = :p WHERE case_id = :cid"),
                {"p": provisions if provisions else None, "cid": case_id},
            )
            stats[source] += 1

        db.commit()

    total = stats["pass1"] + stats["pass2"] + stats["null"]
    logger.info(f"Done. Total processed: {total}")
    logger.info(f"  Pass 1 (primary fields):  {stats['pass1']:>5} ({stats['pass1']/total*100:.1f}%)")
    logger.info(f"  Pass 2 (all fields ≥2):   {stats['pass2']:>5} ({stats['pass2']/total*100:.1f}%)")
    logger.info(f"  Still null:               {stats['null']:>5} ({stats['null']/total*100:.1f}%)")
    logger.info(f"  Not in DB (skipped):      {stats['not_in_db']}")


if __name__ == "__main__":
    main()
