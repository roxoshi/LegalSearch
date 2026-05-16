"""
Extract (section, act) pairs from analysis JSONs and store in documents.act_sections.

Strategy:
  1. Direct pair: regex finds "Section 16 of/under the CGST Act" in one pass
  2. Context pair: section within 100 chars of an Act mention → associate
  3. Dominant Act fallback: orphan sections get paired with the most-mentioned Act
     in the full document text

Two-pass fallback (same as extract_provisions.py):
  Pass 1 — primary fields only: issues + ratio_decidendi + conclusion
  Pass 2 — all 10 fields (if pass 1 found nothing)

Stored as text[] with pipe-delimited strings: "Section 16|CGST Act"
Column: documents.act_sections (text[])
"""

import json
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etl.database import get_db_url, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ── Act patterns → canonical name ─────────────────────────────────────────────
#
# Order matters: check more specific patterns before generic ones.
#
_ACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # CGST Act / Central GST Act / Central Goods and Services Tax Act
    (re.compile(r"\bC\.?G\.?S\.?T\.?\s+Act\b", re.IGNORECASE), "CGST Act"),
    (re.compile(r"\bCentral\s+Goods\s+and\s+Services\s+Tax\s+Act\b", re.IGNORECASE), "CGST Act"),
    # IGST Act
    (re.compile(r"\bI\.?G\.?S\.?T\.?\s+Act\b", re.IGNORECASE), "IGST Act"),
    (re.compile(r"\bIntegrated\s+Goods\s+and\s+Services\s+Tax\s+Act\b", re.IGNORECASE), "IGST Act"),
    # UTGST Act
    (re.compile(r"\bU\.?T\.?G\.?S\.?T\.?\s+Act\b", re.IGNORECASE), "UTGST Act"),
    (re.compile(r"\bUnion\s+Territory\s+Goods\s+and\s+Services\s+Tax\s+Act\b", re.IGNORECASE), "UTGST Act"),
    # GST (Compensation to States) Act / Compensation Cess Act
    (re.compile(
        r"\bGST\s*\(?Compensation\s+to\s+States\)?\s+Act\b"
        r"|\bCompensation\s+(?:Cess\s+)?Act\b"
        r"|\bGoods\s+and\s+Services\s+Tax\s+\(?Compensation\s+to\s+States\)?\s+Act\b",
        re.IGNORECASE,
    ), "Compensation Act"),
    # Constitution (101st Amendment) Act
    (re.compile(
        r"\bConstitution\s+\(?101st\s+Amendment\)?\s+Act\b"
        r"|\b101st\s+(?:Constitutional\s+)?Amendment\s+Act\b",
        re.IGNORECASE,
    ), "Constitution (101st Amendment) Act"),
    # Generic "GST Act" fallback (when no specific act is named) — map to CGST Act
    # because in Indian GST jurisprudence "the GST Act" almost always means CGST Act
    (re.compile(r"\bGST\s+Act\b", re.IGNORECASE), "CGST Act"),
    # State GST Acts (OGST, TNGST, BGST, KGST, APGST, DGST, SGST, etc.) are mirror
    # legislation of CGST Act — same sections, same legal questions. Merge into CGST Act
    # so users can filter "CGST Act" and get both central and state variants.
    (re.compile(r"\b[A-Z]{1,4}GST\s+Act\b"), "CGST Act"),
]

# ── CGST Rules / IGST Rules (for Rule X pairings) ─────────────────────────────
_RULES_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bC\.?G\.?S\.?T\.?\s+Rules\b", re.IGNORECASE), "CGST Rules"),
    (re.compile(r"\bI\.?G\.?S\.?T\.?\s+Rules\b", re.IGNORECASE), "IGST Rules"),
    (re.compile(r"\bU\.?T\.?G\.?S\.?T\.?\s+Rules\b", re.IGNORECASE), "UTGST Rules"),
    (re.compile(r"\bGST\s+Rules\b", re.IGNORECASE), "CGST Rules"),
    # State GST Rules are mirror of CGST Rules — merge into CGST Rules
    (re.compile(r"\b[A-Z]{1,4}GST\s+Rules\b"), "CGST Rules"),
]

# Combine into one lookup (acts + rules)
_ALL_STATUTE_PATTERNS = _ACT_PATTERNS + _RULES_PATTERNS


# ── Provision regex ────────────────────────────────────────────────────────────
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


def _normalise_provision(match: str) -> str:
    parts = match.strip().split(None, 1)
    return parts[0].capitalize() + " " + parts[1] if len(parts) == 2 else match.strip()


def _detect_act(fragment: str) -> str | None:
    """Return canonical Act/Rules name if any statute pattern matches in fragment."""
    for pattern, canonical in _ALL_STATUTE_PATTERNS:
        if pattern.search(fragment):
            return canonical
    return None


def _extract_pairs_from_text(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """
    Return (direct_pairs, orphan_provisions).

    direct_pairs  — (provision, act) when an Act was found within ±100 chars of
                    the provision mention.
    orphan_provisions — provisions with no nearby Act mention.
    """
    direct: list[tuple[str, str]] = []
    orphans: list[str] = []

    for m in _PROVISION_RE.finditer(text):
        prov = _normalise_provision(m.group(0))
        start, end = m.start(), m.end()

        # Look in a window: 20 chars before to 120 chars after (of/under/the … Act)
        window = text[max(0, start - 20): end + 120]
        act = _detect_act(window)
        if act:
            direct.append((prov, act))
        else:
            orphans.append(prov)

    return direct, orphans


def _dominant_act(text: str) -> str | None:
    """Return the most frequently mentioned Act across the full text, or None."""
    counts: Counter[str] = Counter()
    for pattern, canonical in _ACT_PATTERNS:
        counts[canonical] += len(pattern.findall(text))
    if not counts:
        return None
    top, freq = counts.most_common(1)[0]
    return top if freq > 0 else None


def _dedupe_ordered(pairs: list[tuple[str, str]]) -> list[str]:
    seen: set[tuple[str, str]] = set()
    out: list[str] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            out.append(f"{pair[0]}|{pair[1]}")
    return out


def extract_pairs(data: dict, fields: tuple[str, ...]) -> list[str]:
    """
    Extract (section, act) pairs from the given fields.

    1. Collect direct pairs + orphans from each field.
    2. Determine dominant Act across all given field texts.
    3. Pair orphans with dominant Act (if any).
    4. Return deduplicated "Section X|Act Y" strings.
    """
    all_direct: list[tuple[str, str]] = []
    all_orphans: list[str] = []
    combined_text = " ".join((data.get(f, "") or "") for f in fields)

    for field in fields:
        text_val = data.get(field, "") or ""
        direct, orphans = _extract_pairs_from_text(text_val)
        all_direct.extend(direct)
        all_orphans.extend(orphans)

    dom = _dominant_act(combined_text)

    if dom and all_orphans:
        for orphan in all_orphans:
            all_direct.append((orphan, dom))

    return _dedupe_ordered(all_direct)


def main() -> None:
    analysis_dir = Path(os.getenv("ANALYSIS_DIR", ".data/batch_analysis/successes"))
    if not analysis_dir.exists():
        logger.error(f"Analysis dir not found: {analysis_dir}")
        sys.exit(1)

    SessionLocal = init_db()

    with SessionLocal() as db:
        db.execute(text("""
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS act_sections text[];
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_documents_act_sections
            ON documents USING gin(act_sections);
        """))
        db.commit()
        logger.info("Column act_sections and GIN index ready.")

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

            # Pass 1: primary fields
            pairs = extract_pairs(data, PRIMARY_FIELDS)
            source = "pass1"

            # Pass 2 fallback: all fields
            if not pairs:
                pairs = extract_pairs(data, ALL_FIELDS)
                source = "pass2"

            if not pairs:
                source = "null"

            result = db.execute(
                text("SELECT id FROM documents WHERE case_id = :cid"),
                {"cid": case_id},
            ).first()

            if result is None:
                stats["not_in_db"] += 1
                continue

            db.execute(
                text("UPDATE documents SET act_sections = :p WHERE case_id = :cid"),
                {"p": pairs if pairs else None, "cid": case_id},
            )
            stats[source] += 1

        db.commit()

    total = stats["pass1"] + stats["pass2"] + stats["null"]
    if total == 0:
        logger.warning("No documents processed.")
        return
    logger.info(f"Done. Total processed: {total}")
    logger.info(f"  Pass 1 (primary fields):  {stats['pass1']:>5} ({stats['pass1']/total*100:.1f}%)")
    logger.info(f"  Pass 2 (all fields):      {stats['pass2']:>5} ({stats['pass2']/total*100:.1f}%)")
    logger.info(f"  Still null:               {stats['null']:>5} ({stats['null']/total*100:.1f}%)")
    logger.info(f"  Not in DB (skipped):      {stats['not_in_db']}")


if __name__ == "__main__":
    main()
