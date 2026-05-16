"""
Convert notification/circular PDFs to clean structured HTML using PyMuPDF.

Strategy:
  - Extract text blocks, skipping any that overlap with detected table regions.
  - Render tables as proper <table> HTML.
  - Strip gazette header (everything up to and including the G.S.R. line) and
    footer (from [F. No. onwards).
  - Join soft-wrapped lines within each text block.

Usage:
    # Preview a single PDF (stdout)
    uv run python -m etl.process_notifications --pdf .data/notifications/pdfs/central_tax/2025/20_2025-CentralTax.pdf

    # Process all notifications and write structured_html to DB
    uv run python -m etl.process_notifications --all

    # Process one category
    uv run python -m etl.process_notifications --category central_tax
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

NOTIF_DIR = Path(os.getenv("NOTIF_DIR", ".data/notifications"))
PDF_DIR = NOTIF_DIR / "pdfs"
JSON_DIR = NOTIF_DIR / "jsons"

# Characters that end a sentence / clause — line break after these is a hard break
_HARD_BREAK_RE = re.compile(r'[.;]\s*$')
# Numbered item starter: "1.\n" or "(1)\n" at beginning of block
_ITEM_RE = re.compile(r'^(\d+\.|[(\[]\d+[)\]])\s+', re.MULTILINE)

# Gazette header: everything up to and including the G.S.R. preamble line
_HEADER_MARK = "G.S.R"
# Footer marker
_FOOTER_MARKS = ["[F. No.", "Note: The principal"]


def _overlaps(a: tuple, b: tuple) -> bool:
    """Return True if rect a and b overlap (even partially)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0


def _table_to_html(rows: list[list[Any]]) -> str:
    """Convert extracted table rows to an HTML <table> string."""
    if not rows:
        return ""

    def cell(text: str, tag: str) -> str:
        safe = html.escape(str(text or "").replace("\n", " ").strip())
        return f"<{tag}>{safe}</{tag}>"

    lines = ['<table class="notif-table">']

    # First row is the header
    header = rows[0]
    lines.append("  <thead><tr>")
    for c in header:
        lines.append(f"    {cell(c, 'th')}")
    lines.append("  </tr></thead>")

    # Skip a row that is purely "(1) (2) (3)..." column-index labels
    data_rows = rows[1:]
    if data_rows and all(
        re.fullmatch(r'\(\d+\)', str(c or "").strip()) for c in data_rows[0] if str(c or "").strip()
    ):
        data_rows = data_rows[1:]

    lines.append("  <tbody>")
    for row in data_rows:
        lines.append("  <tr>")
        for c in row:
            lines.append(f"    {cell(c, 'td')}")
        lines.append("  </tr>")
    lines.append("  </tbody>")
    lines.append("</table>")
    return "\n".join(lines)


def _join_soft_wraps(text: str) -> str:
    """Join lines within a block that were soft-wrapped (not true paragraph breaks)."""
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            result.append("")
            i += 1
            continue
        # Greedily extend by joining as many consecutive soft-wrapped lines as possible
        while True:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines):
                break
            next_line = lines[j].strip()
            # Soft wrap: current line doesn't end in sentence-ending punctuation
            # and next line continues (starts lowercase/digit)
            if (not _HARD_BREAK_RE.search(line)
                    and next_line
                    and (next_line[0].islower() or next_line[0].isdigit())):
                line = line + " " + next_line
                i = j  # advance so next iteration checks j+1
            else:
                break
        result.append(line)
        i += 1

    return "\n".join(result).strip()


def _format_block(text: str) -> str:
    """Convert a text block to one or more HTML <p> elements."""
    text = _join_soft_wraps(text)
    # Split on blank lines for sub-paragraphs within a block
    paras = re.split(r'\n\s*\n', text)
    parts = []
    for para in paras:
        para = para.strip()
        if not para:
            continue
        safe = html.escape(para).replace("\n", "<br>")
        parts.append(f"<p>{safe}</p>")
    return "\n".join(parts)


def pdf_to_html(pdf_path: Path) -> str:
    """Convert a notification/circular PDF to structured HTML."""
    try:
        import fitz
        fitz.TOOLS.mupdf_display_errors(False)
    except ImportError:
        log.error("PyMuPDF not installed — run: uv add pymupdf")
        return ""

    doc = fitz.open(str(pdf_path))
    if not doc.page_count:
        return ""

    # (top_y, bottom_y, type, content)
    all_elements: list[tuple[float, float, str, str]] = []

    for page_num, page in enumerate(doc):
        page_h = page.rect.height
        y_off = page_num * page_h  # stack pages vertically for global ordering

        try:
            tabs = page.find_tables()
        except Exception:
            tabs = None
        table_bboxes = [t.bbox for t in tabs.tables] if tabs else []

        # Add tables
        for t in (tabs.tables if tabs else []):
            table_html = _table_to_html(t.extract())
            if table_html:
                all_elements.append((y_off + t.bbox[1], y_off + t.bbox[3], "table", table_html))

        # Add text blocks, excluding those inside table regions
        blocks = page.get_text("blocks", sort=True)
        for b in blocks:
            x0, y0, x1, y1, text, _bno, btype = b
            if btype != 0:  # skip image/drawing blocks
                continue
            text = text.strip()
            if not text:
                continue
            # Skip blocks overlapping a table bbox
            if any(_overlaps((x0, y0, x1, y1), tb) for tb in table_bboxes):
                continue
            # Skip "Table" label that precedes the table
            if re.fullmatch(r'Table\.?', text):
                continue
            all_elements.append((y_off + y0, y_off + y1, "text", text))

    all_elements.sort(key=lambda e: e[0])

    # Merge consecutive adjacent text blocks that belong to the same paragraph.
    # Some PDFs emit each wrapped line as its own block (typewriter-style layout).
    # Use bottom_y of previous block vs top_y of current for gap measurement.
    merged: list[tuple[float, float, str, str]] = []
    for top_y, bot_y, typ, content in all_elements:
        if (typ == "text" and merged and merged[-1][2] == "text"):
            prev_top, prev_bot, _, prev_text = merged[-1]
            gap = top_y - prev_bot  # gap between bottom of prev and top of current
            prev_last_line = prev_text.rstrip()
            first_char = content.strip()[:1]
            # Join if: small gap AND previous line doesn't hard-end
            # AND next content looks like a continuation (lowercase, digit, or opening quotes)
            cs = content.strip()
            # New numbered item (e.g. "1.\n", "2.") → always a new paragraph
            _is_new_item = bool(re.match(r'^\d+\.\s', cs))
            # Never merge if the next block is a footer marker — it must stay separate
            # so the footer detection below doesn't collapse it with the body.
            _is_footer = any(cs.startswith(m) for m in _FOOTER_MARKS)
            if (gap < 10 and not _HARD_BREAK_RE.search(prev_last_line) and cs
                    and not _is_new_item and not _is_footer):
                # Very small gap → almost certainly a soft-wrapped line
                merged[-1] = (prev_top, bot_y, "text", prev_last_line + " " + cs)
                continue
            if (gap < 20 and not _HARD_BREAK_RE.search(prev_last_line) and cs
                    and not _is_new_item
                    and (cs[0].islower() or cs[0] in ('"', "'", '\u201c', '\u2018'))):
                # Moderate gap + lowercase/quote start → continuation in paragraph-block PDFs
                merged[-1] = (prev_top, bot_y, "text", prev_last_line + " " + cs)
                continue
        merged.append((top_y, bot_y, typ, content))

    # Identify header end and footer start by content
    header_end_idx = 0
    footer_start_idx = len(merged)

    for i, (_top, _bot, typ, content) in enumerate(merged):
        if typ == "text" and _HEADER_MARK in content and header_end_idx == 0:
            header_end_idx = i  # include the G.S.R. block (it's the legal citation)
        if typ == "text" and any(m in content for m in _FOOTER_MARKS):
            footer_start_idx = i
            break

    body = merged[header_end_idx:footer_start_idx]

    parts = []
    for _top, _bot, typ, content in body:
        if typ == "table":
            parts.append(content)
        else:
            parts.append(_format_block(content))

    return "\n".join(parts)


# ── DB integration ────────────────────────────────────────────────────────────

CATEGORY_FILENAME_MAP: dict[str, str] = {
    "central_tax": "central_tax_notifications.json",
    "central_tax_rate": "central_tax_rate_notifications.json",
    "compensation_cess": "compensation_cess_notifications.json",
    "compensation_cess_rate": "compensation_cess_rate_notifications.json",
    "integrated_tax": "integrated_tax_notifications.json",
    "integrated_tax_rate": "integrated_tax_rate_notifications.json",
    "union_territory_tax": "union_territory_tax_notifications.json",
    "union_territory_tax_rate": "union_territory_tax_rate_notifications.json",
}


def _find_pdf(category: str, notif_no: str, date_str: str) -> Path | None:
    """Try to locate a PDF for a notification by notification number and year."""
    cat_dir = PDF_DIR / category
    if not cat_dir.exists():
        return None

    # Parse year from date_str (ISO format: 2025-03-13T...)
    year = date_str[:4] if date_str else None
    year_dirs = [cat_dir / year] if year else sorted(cat_dir.iterdir())

    # Normalise notification number: "20/2025-Central Tax" → "20"
    m = re.match(r'(\d+)', notif_no.strip())
    if not m:
        return None
    num = m.group(1).zfill(2)

    for ydir in year_dirs:
        if not ydir.is_dir():
            continue
        # Look for PDFs whose name starts with the zero-padded number
        for pdf in sorted(ydir.glob(f"{num}_*.pdf")):
            return pdf
        # Try without zero-padding
        for pdf in sorted(ydir.glob(f"{int(num)}_*.pdf")):
            return pdf
    return None


def process_category(category: str, session) -> int:
    json_path = JSON_DIR / CATEGORY_FILENAME_MAP[category]
    if not json_path.exists():
        log.warning("JSON not found: %s", json_path)
        return 0

    with open(json_path) as f:
        records = json.load(f)

    try:
        from backend.app.models import Notification
    except ImportError:
        from app.models import Notification  # type: ignore

    updated = 0
    for rec in records:
        primary_id = rec.get("primary_id")
        notif_no = rec.get("notification_no", "")
        date_str = rec.get("date", "")

        if not primary_id:
            continue

        pdf_path = _find_pdf(category, notif_no, date_str)
        if not pdf_path:
            continue

        structured_html = pdf_to_html(pdf_path)
        if not structured_html:
            continue
        structured_html = structured_html.replace("\x00", "")

        row = session.query(Notification).filter(Notification.primary_id == primary_id).first()
        if row is None:
            continue
        row.structured_html = structured_html
        updated += 1

    session.commit()
    log.info("[%s] Updated %d/%d notifications with structured HTML.", category, updated, len(records))
    return updated


def process_all(session) -> None:
    total = 0
    for category in CATEGORY_FILENAME_MAP:
        total += process_category(category, session)
    log.info("Done. Total updated: %d", total)


# ── Circulars ─────────────────────────────────────────────────────────────────

CIRCULAR_DIR = Path(os.getenv("CIRCULAR_DIR", ".data/circulars"))
CIRCULAR_PDF_DIR = CIRCULAR_DIR / "pdfs"
CIRCULAR_JSON_DIR = CIRCULAR_DIR / "jsons"

CIRCULAR_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    # key → (json_filename, pdf_subdir)
    "cgst": ("cgst_circulars.json", "cgst"),
    "igst": ("igst_circulars.json", "igst"),
    "cess": ("cess_circulars.json", "cess"),
}


def _find_circular_pdf(cat_dir: Path, circular_no: str, date_str: str) -> Path | None:
    year = date_str[:4] if date_str else None
    year_dirs = [cat_dir / year] if (year and (cat_dir / year).is_dir()) else sorted(cat_dir.iterdir())

    # "244/01/2025-GST" → first numeric segment "244"
    m = re.match(r'(\d+)', circular_no.strip())
    if not m:
        return None
    num = m.group(1).zfill(2)

    for ydir in year_dirs:
        if not ydir.is_dir():
            continue
        for pdf in sorted(ydir.glob(f"{num}_*.pdf")):
            return pdf
        for pdf in sorted(ydir.glob(f"{int(num)}_*.pdf")):
            return pdf

    # Some circulars don't have year subdirs — try flat
    for pdf in sorted(cat_dir.glob(f"{num}_*.pdf")):
        return pdf
    return None


def process_circulars(session) -> int:
    try:
        from backend.app.models import Circular
    except ImportError:
        from app.models import Circular  # type: ignore

    total = 0
    for key, (json_file, pdf_subdir) in CIRCULAR_CATEGORY_MAP.items():
        json_path = CIRCULAR_JSON_DIR / json_file
        if not json_path.exists():
            log.warning("Circular JSON not found: %s", json_path)
            continue

        with open(json_path) as f:
            records = json.load(f)

        cat_pdf_dir = CIRCULAR_PDF_DIR / pdf_subdir
        updated = 0
        for rec in records:
            primary_id = rec.get("primary_id")
            circular_no = rec.get("circular_no", "")
            date_str = rec.get("date", "")
            if not primary_id:
                continue

            pdf_path = _find_circular_pdf(cat_pdf_dir, circular_no, date_str)
            if not pdf_path:
                continue

            structured_html = pdf_to_html(pdf_path)
            if not structured_html:
                continue
            structured_html = structured_html.replace("\x00", "")

            row = session.query(Circular).filter(Circular.primary_id == primary_id).first()
            if row is None:
                continue
            row.structured_html = structured_html
            updated += 1

        session.commit()
        log.info("[circulars/%s] Updated %d/%d with structured HTML.", key, updated, len(records))
        total += updated
    return total


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Convert notification/circular PDFs to structured HTML")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pdf", type=Path, help="Preview a single PDF (prints HTML to stdout)")
    mode.add_argument("--all", action="store_true", help="Process all notifications + circulars into DB")
    mode.add_argument("--circulars", action="store_true", help="Process all circulars into DB")
    mode.add_argument("--category", choices=list(CATEGORY_FILENAME_MAP), help="Process one notification category into DB")
    args = parser.parse_args()

    if args.pdf:
        result = pdf_to_html(args.pdf)
        print(result)
        return

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from etl.database import init_db
    SessionFactory = init_db()
    session = SessionFactory()
    try:
        if args.all:
            process_all(session)
            process_circulars(session)
        elif args.circulars:
            process_circulars(session)
        else:
            process_category(args.category, session)
    except Exception:
        session.rollback()
        log.exception("Failed — rolled back.")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
