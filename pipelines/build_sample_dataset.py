"""
Generate a 50-row sample CSV for manual label verification.
- 25 positives: sampled from .data/gst_pdfs/
- 25 negatives: sampled from .data/staging/hc_pdfs/, stage-1 rejects only
  (excludes any file already in gst_pdfs)

Output: .data/classifier_sample.csv  with columns: file, label, text
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipelines.optimizations import keyword_prefilter

POSITIVE_DIR = Path(".data/gst_pdfs")
NEGATIVE_DIR = Path(".data/staging/hc_pdfs")
OUTPUT_CSV = Path(".data/classifier_sample.csv")

N_POSITIVE = 25
N_NEGATIVE = 25
TEXT_CHARS = 6000


def extract_text(pdf_path: Path) -> str | None:
    try:
        doc = fitz.open(pdf_path)
        fitz.TOOLS.mupdf_display_errors(False)
        text = ""
        for page in doc:
            text += page.get_text()
            if len(text) >= TEXT_CHARS * 2:
                break
        doc.close()
        return text[:TEXT_CHARS].strip()
    except Exception:
        return None


def sample_positives(n: int) -> list[dict]:
    pdfs = list(POSITIVE_DIR.glob("*.pdf"))
    random.shuffle(pdfs)
    rows = []
    for pdf in pdfs:
        if len(rows) >= n:
            break
        text = extract_text(pdf)
        if text:
            rows.append({"file": pdf.name, "label": 1, "text": text})
    return rows


def sample_negatives(n: int, exclude: set[str]) -> list[dict]:
    pdfs = list(NEGATIVE_DIR.glob("*.pdf"))
    random.shuffle(pdfs)
    rows = []
    for pdf in pdfs:
        if len(rows) >= n:
            break
        if pdf.name in exclude:
            continue
        text = extract_text(pdf)
        if not text:
            continue
        is_gst, _ = keyword_prefilter(text)
        if not is_gst:
            rows.append({"file": pdf.name, "label": 0, "text": text})
    return rows


def main() -> None:
    random.seed(42)

    print("Sampling positives...")
    positives = sample_positives(N_POSITIVE)
    print(f"  Got {len(positives)} positives")

    exclude = {p["file"] for p in positives}
    print("Sampling negatives (stage-1 rejects only)...")
    negatives = sample_negatives(N_NEGATIVE, exclude)
    print(f"  Got {len(negatives)} negatives")

    rows = positives + negatives
    random.shuffle(rows)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "label", "text"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
