import argparse
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

import fitz
from bs4 import BeautifulSoup

# logging
logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("pdf_conversion")


# Legitimate section headings in legal documents
HEADING_KEYWORDS = frozenset(
    [
        "judgment",
        "order",
        "headnote",
        "factual matrix",
        "facts",
        "issue for consideration",
        "issues for consideration",
        "case law cited",
        "appearances for parties",
        "appearances",
        "list of acts",
        "list of keywords",
        "case arising from",
        "books and periodicals cited",
        "conclusion",
        "arguments",
        "submissions",
        "analysis",
        "discussion",
        "background",
        "ratio decidendi",
        "obiter dicta",
        "operative part",
        "prayer",
        "relief",
        "decree",
        "result",
        "decision",
    ]
)

# Patterns that should NEVER be headings
NON_HEADING_PATTERNS = [
    re.compile(r"^\[.*(?:JJ?\.?|JUSTICE|Judge).*\]$", re.IGNORECASE),  # Judge names
    re.compile(r"^\(.*(?:Appeal|Petition|Writ|Case).*\d+.*\)$", re.IGNORECASE),  # Case numbers
    re.compile(r"\bv\.?\s*$", re.IGNORECASE),  # Party name ending with "v."
    re.compile(r"^[A-Z]\.$"),  # Single letter with period
    re.compile(r"^\d+\.$"),  # Numbered items
    re.compile(
        r"^(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d",
        re.IGNORECASE,
    ),  # Dates
]


def analyze_marker_columns(doc):
    """
    Pass 1: Scan the entire document to identify vertical bands (columns)
    where single-character junk markers (A-H) frequently appear.
    """
    bins: dict[int, int] = defaultdict(int)
    page_count = len(doc)

    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] == 0:
                text = "".join(
                    "".join(s["text"] for s in line["spans"]) for line in b["lines"]
                ).strip()
                if len(text) == 1 and text.upper() in "ABCDEFGH":
                    x_center = (b["bbox"][0] + b["bbox"][2]) / 2
                    bin_idx = int(x_center // 5) * 5
                    bins[bin_idx] += 1

    threshold = max(2, page_count * 0.25)
    marker_columns = [b for b, count in bins.items() if count >= threshold]
    return marker_columns


def clean_text(text):
    """Clean and normalize extracted text."""
    # Unicode replacements using escape sequences
    replacements = {
        "\u2013": "-",  # en-dash
        "\u2014": "--",  # em-dash
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2018": "'",  # left single quote
        "\u2019": "'",  # right single quote
        "\u2026": "...",  # ellipsis
        "\u00c2": "",  # Â artifact
        "\u2020": "",  # dagger
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return " ".join(text.split())


def fix_text_artifacts(text):
    """Post-process text to fix common extraction artifacts."""
    # Fix merged "of" with year: "of2016" -> "of 2016"
    text = re.sub(r"\bof(\d{4})\b", r"of \1", text)
    text = re.sub(r"\b(\d{4})of\b", r"\1 of", text)

    # Fix common OCR errors
    text = re.sub(r"n'o\b", "no", text)
    text = re.sub(r"\.~", ".", text)
    text = re.sub(r"injw:v", "injury", text)
    text = re.sub(r"injuncti\(ln", "injunction", text)
    text = re.sub(r"fude", "facie", text)  # prima fude -> prima facie

    # Fix broken hyphenation (word- continuation)
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)

    # Remove isolated margin markers (A-H) that appear between words
    # Match: word boundary, space, single A-H letter, space, word boundary
    text = re.sub(r"(?<=\s)[A-H](?=\s+[A-Za-z])", "", text)
    # Also at start of text
    text = re.sub(r"^[A-H]\s+(?=[A-Za-z])", "", text)

    # Fix possessive apostrophes
    text = re.sub(r"(\w)[''`]s\b", r"\1's", text)
    text = re.sub(r"(\w)'\.", r"\1's", text)  # respondent'. -> respondent's

    # Remove isolated page numbers like "133", "135" at end of paragraph
    text = re.sub(r"\s+\d{2,3}\s*$", "", text)

    # Clean up multiple spaces
    text = re.sub(r"\s{2,}", " ", text)

    return text.strip()


def is_header_footer(text, bbox, page_height):
    """Detect header/footer content to be filtered out."""
    stripped_text = text.strip()
    if stripped_text.isdigit() and len(stripped_text) < 5:
        return True

    lower_text = stripped_text.lower()
    if "supreme court reports" in lower_text:
        return True
    if stripped_text.startswith("[") and "S.C.R." in stripped_text:
        return True

    y0, y1 = bbox[1], bbox[3]
    return bool((y1 < page_height * 0.12 or y0 > page_height * 0.92) and len(stripped_text) < 140)


def is_margin_junk(text, bbox, marker_columns):
    """Filter single-character margin markers using detected columns."""
    stripped = text.strip()
    if len(stripped) == 1:
        x_center = (bbox[0] + bbox[2]) / 2
        bin_idx = int(x_center // 5) * 5
        if bin_idx in marker_columns:
            return True
        # Fallback: extreme margins
        if bbox[2] < 50 or bbox[0] > 400:
            return True
    return False


def is_valid_heading(text, is_bold, bbox, page_height):
    """
    Strict heading detection - only recognize genuine section headings.

    A block is a heading if:
    1. It's bold
    2. It's in the main content area (not header/footer)
    3. It contains a known heading keyword OR is all-caps and short
    4. It does NOT match any non-heading pattern
    """
    stripped = text.strip()

    # Too short or too long
    if len(stripped) <= 2 or len(stripped) > 100:
        return False

    # Must be bold
    if not is_bold:
        return False

    # Check position - not in header/footer zone
    y0 = bbox[1]
    if y0 < page_height * 0.12 or y0 > page_height * 0.90:
        return False

    # Check against non-heading patterns (judge names, case numbers, etc.)
    for pattern in NON_HEADING_PATTERNS:
        if pattern.search(stripped):
            return False

    stripped.upper()
    lower_text = stripped.lower()

    # Check for heading keywords
    if any(kw in lower_text for kw in HEADING_KEYWORDS):
        return True

    # All-caps text that's moderately sized could be a heading
    if stripped.isupper() and 4 < len(stripped) < 60:
        # But filter out things that look like party names
        if " v. " in stripped or " V. " in stripped:
            return False
        return not (stripped.endswith(", JJ.]") or "JUSTICE" in stripped)

    return False


def should_merge_blocks(prev_text, _curr_text, curr_starts_lower):
    """
    Determine if two blocks should be merged into one paragraph.

    Blocks are merged if:
    - Previous text doesn't end with sentence-ending punctuation
    - Current text starts with lowercase
    - Current text is a continuation (not a new section)
    """
    if not prev_text:
        return False

    prev_stripped = prev_text.strip()
    if not prev_stripped:
        return False

    # Check if previous ends with sentence-ending punctuation
    ends_sentence = prev_stripped[-1] in '.?!":;'

    # If previous doesn't end sentence, merge
    if not ends_sentence:
        return True

    # If current starts lowercase, it's likely a continuation
    return bool(curr_starts_lower)


def merge_line_spans(line_spans):
    """
    Merge adjacent spans with the same formatting into single spans.
    This reduces fragmentation from word-by-word extraction.
    """
    if not line_spans:
        return []

    merged = []
    current = {"text": line_spans[0]["text"], "is_bold": line_spans[0]["is_bold"]}

    for span in line_spans[1:]:
        if span["is_bold"] == current["is_bold"]:
            current["text"] += " " + span["text"]
        else:
            merged.append(current)
            current = {"text": span["text"], "is_bold": span["is_bold"]}

    merged.append(current)
    return merged


def extract_blocks_from_page(page, marker_columns):
    """Extract structured blocks from a PDF page."""
    page_height = page.rect.height
    blocks = page.get_text("dict")["blocks"]
    structured = []

    for b in blocks:
        if b["type"] != 0:
            continue

        block_spans = []
        block_text_parts = []
        block_is_bold = False

        for line in b["lines"]:
            line_spans = []
            for span in line["spans"]:
                span_text = clean_text(span["text"])
                if not span_text.strip():
                    continue

                is_bold = bool(span["flags"] & 2**4) or "Bold" in span["font"]
                line_spans.append({"text": span_text, "is_bold": is_bold})
                block_text_parts.append(span_text)
                if is_bold:
                    block_is_bold = True

            if line_spans:
                # Merge adjacent spans with same formatting
                merged_spans = merge_line_spans(line_spans)
                block_spans.append(merged_spans)

        full_text = " ".join(block_text_parts)
        cleaned_text = clean_text(full_text)

        if not cleaned_text:
            continue

        # Filter junk - check both the full text and individual components
        if is_margin_junk(cleaned_text, b["bbox"], marker_columns):
            continue
        if is_header_footer(cleaned_text, b["bbox"], page_height):
            continue

        # Apply text artifact fixes
        cleaned_text = fix_text_artifacts(cleaned_text)

        # Filter out isolated single letters that leaked through
        # (margin markers that weren't in known columns)
        if len(cleaned_text) == 1 and cleaned_text.upper() in "ABCDEFGH":
            continue

        structured.append(
            {
                "text": cleaned_text,
                "spans": block_spans,
                "is_bold": block_is_bold,
                "is_heading": is_valid_heading(cleaned_text, block_is_bold, b["bbox"], page_height),
                "bbox": b["bbox"],
            }
        )

    return structured


def merge_paragraphs(blocks):
    """
    Merge consecutive non-heading blocks into logical paragraphs.

    This addresses the fragmentation issue where PDF extraction
    creates separate blocks for what should be continuous text.
    """
    merged = []
    current_para = None

    for block in blocks:
        if block["is_heading"]:
            # Flush current paragraph
            if current_para:
                merged.append(current_para)
                current_para = None
            merged.append(
                {
                    "type": "heading",
                    "text": block["text"],
                }
            )
            continue

        # Determine if this should merge with previous
        if current_para is None:
            current_para = {
                "type": "paragraph",
                "text": block["text"],
                "spans": block["spans"],
            }
        else:
            curr_starts_lower = block["text"] and block["text"][0].islower()
            if should_merge_blocks(current_para["text"], block["text"], curr_starts_lower):
                # Merge: append text and spans
                current_para["text"] += " " + block["text"]
                current_para["spans"].extend(block["spans"])
            else:
                # Start new paragraph
                merged.append(current_para)
                current_para = {
                    "type": "paragraph",
                    "text": block["text"],
                    "spans": block["spans"],
                }

    if current_para:
        merged.append(current_para)

    return merged


def build_html(elements):
    """Construct HTML from merged elements."""
    soup = BeautifulSoup(
        "<html><head><meta charset='utf-8'></head><body></body></html>", "html.parser"
    )
    assert soup.head is not None
    assert soup.body is not None
    body = soup.body

    # CSS styles
    style = soup.new_tag("style")
    style.string = """
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.7;
            color: #2d3748;
            max-width: 850px;
            margin: 0 auto;
            padding: 40px 20px;
            background-color: #fff;
        }
        p { margin-bottom: 1.25em; text-align: justify; }
        strong { font-weight: 700; color: #1a202c; }
        h2 {
            font-size: 1.4em;
            font-weight: 700;
            margin-top: 2.5em;
            margin-bottom: 0.8em;
            color: #2c5282;
            border-bottom: 2px solid #ebf8ff;
            padding-bottom: 0.2em;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }
    """
    soup.head.append(style)

    for elem in elements:
        if elem["type"] == "heading":
            tag = soup.new_tag("h2")
            tag.string = elem["text"]
            body.append(tag)
        else:
            p_tag = soup.new_tag("p")
            # Reconstruct with bold spans
            for line_spans in elem.get("spans", []):
                for span in line_spans:
                    span_text = fix_text_artifacts(span["text"])
                    if span["is_bold"]:
                        bold_tag = soup.new_tag("strong")
                        bold_tag.string = span_text
                        p_tag.append(bold_tag)
                    else:
                        p_tag.append(span_text)
                p_tag.append(" ")
            body.append(p_tag)

    return soup.prettify()


def convert_pdf_to_html(pdf_path: Path) -> str:
    """
    Convert a PDF to HTML using multi-pass processing:
    1. Identify marker columns globally
    2. Extract and filter blocks per page
    3. Merge into logical paragraphs
    4. Build clean HTML
    """
    try:
        doc = fitz.open(pdf_path)

        # Pass 1: Global marker column analysis
        marker_columns = analyze_marker_columns(doc)
        logger.debug(f"Marker columns for {pdf_path.name}: {marker_columns}")

        # Pass 2: Extract structured blocks from all pages
        all_blocks = []
        for page in doc:
            page_blocks = extract_blocks_from_page(page, marker_columns)
            all_blocks.extend(page_blocks)

        # Pass 3: Merge into logical paragraphs
        elements = merge_paragraphs(all_blocks)

        # Pass 4: Build HTML
        html = build_html(elements)

        doc.close()
        return html

    except Exception as e:
        logger.error(f"Error converting PDF {pdf_path}: {e}")
        return ""


def pdf_to_html_direct(pdf_path, html_path):
    """Convert a single PDF to HTML file."""
    html_content = convert_pdf_to_html(Path(pdf_path))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def convert_bulk_htmls(source_dir, output_dir):
    """Convert all PDFs in a directory to HTML."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for filename in os.listdir(source_dir):
        if filename.endswith(".pdf"):
            pdf_path = os.path.join(source_dir, filename)
            html_path = os.path.join(output_dir, filename.replace(".pdf", ".html"))
            try:
                pdf_to_html_direct(pdf_path, html_path)
                logger.info("Successfully converted: %s", filename)
            except Exception:
                logger.exception(f"Failed to convert {filename}")


def main():
    parser = argparse.ArgumentParser(description="Convert a directory of PDFs to HTMLs")
    parser.add_argument("source_dir", help="Path to pdf files")
    parser.add_argument("output_dir", help="Path to store html files")
    args = parser.parse_args()
    convert_bulk_htmls(args.source_dir, args.output_dir)
    logger.info(f"Conversion complete. HTMLs written at: {args.output_dir}")


if __name__ == "__main__":
    main()
