"""Build OpenAI Batch API JSONL for new GST case analyses.

Steps:
  1. Load all new CNRs from .data/classifier/scan_*_new_cases.txt
  2. Skip CNRs already analysed (successes dir has {CNR}.json)
  3. Deduplicate by MD5 hash — batch judgments share one PDF across many CNRs
  4. Extract text from each unique PDF; truncate to 128K tokens (head+tail)
  5. Write openai_batch_requests.jsonl  (one line per unique PDF)
  6. Write dedup_map.json               ({primary_cnr: [all_cnrs_sharing_pdf]})

Usage:
    uv run python -m pipelines.build_openai_batch [--output-dir .data/batch_analysis]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

import fitz
import tiktoken

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("build_openai_batch")

TOKEN_LIMIT = 128_000
HEAD_TOKENS = 60_000
TAIL_TOKENS = 68_000  # HEAD + TAIL = 128K
MODEL = "gpt-4o-mini"
MAX_OUTPUT_TOKENS = 2000

SCAN_DIR = Path(".data/classifier")
PDF_DIR = Path(".data/gst_pdfs")
PROMPT_PATH = Path(__file__).parent / "PROMPT.md"


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_new_cnrs(scan_dir: Path, successes_dir: Path) -> list[str]:
    """Load all CNRs from scan files, skipping already-analysed ones."""
    cnrs: set[str] = set()
    for f in sorted(scan_dir.glob("scan_*_new_cases.txt")):
        for line in f.read_text().splitlines():
            parts = line.strip().split()
            if parts:
                cnrs.add(parts[0])

    already_done = {p.stem for p in successes_dir.glob("*.json")}
    pending = [c for c in sorted(cnrs) if c not in already_done]
    logger.info(
        "CNRs total=%d  already_done=%d  pending=%d",
        len(cnrs), len(already_done), len(pending),
    )
    return pending


def group_by_hash(cnrs: list[str], pdf_dir: Path) -> tuple[dict[str, list[str]], int]:
    """Group CNRs by PDF content hash. Returns (hash→cnrs, missing_count)."""
    size_groups: dict[int, list[tuple[str, Path]]] = defaultdict(list)
    missing = 0
    for cnr in cnrs:
        pdf = pdf_dir / f"{cnr}.pdf"
        if not pdf.exists():
            missing += 1
            continue
        size_groups[pdf.stat().st_size].append((cnr, pdf))

    hash_to_cnrs: dict[str, list[str]] = {}
    for size, entries in size_groups.items():
        if len(entries) == 1:
            cnr, pdf = entries[0]
            # Use a sentinel key that won't collide with real MD5s
            hash_to_cnrs[f"uniq_{cnr}"] = [cnr]
        else:
            for cnr, pdf in entries:
                h = hashlib.md5(pdf.read_bytes()).hexdigest()
                hash_to_cnrs.setdefault(h, []).append(cnr)

    return hash_to_cnrs, missing


def extract_text(pdf_path: Path) -> str:
    fitz.TOOLS.mupdf_display_errors(False)
    doc = fitz.open(str(pdf_path))
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text.strip()


def truncate_tokens(text: str, enc: tiktoken.Encoding) -> tuple[str, bool]:
    """Return (text, was_truncated). Uses head+tail strategy."""
    tokens = enc.encode(text, disallowed_special=())
    if len(tokens) <= TOKEN_LIMIT:
        return text, False
    head = tokens[:HEAD_TOKENS]
    tail = tokens[-TAIL_TOKENS:]
    truncated = enc.decode(head) + "\n\n[... middle section omitted for length ...]\n\n" + enc.decode(tail)
    return truncated, True


def build_request(custom_id: str, case_text: str, prompt: str) -> dict:
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": f"{prompt}\n\n# CASE LAW TEXT\n\n{case_text}",
                }
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OpenAI Batch API JSONL")
    parser.add_argument("--output-dir", default=".data/batch_analysis")
    parser.add_argument("--scan-dir", default=str(SCAN_DIR))
    parser.add_argument("--pdf-dir", default=str(PDF_DIR))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    successes_dir = output_dir / "successes"
    successes_dir.mkdir(parents=True, exist_ok=True)

    scan_dir = Path(args.scan_dir)
    pdf_dir = Path(args.pdf_dir)

    prompt = load_prompt()
    enc = tiktoken.get_encoding("cl100k_base")

    # Step 1 — discover pending CNRs
    pending_cnrs = load_new_cnrs(scan_dir, successes_dir)

    # Step 2 — group by content hash
    logger.info("Grouping %d CNRs by PDF content hash...", len(pending_cnrs))
    hash_to_cnrs, missing = group_by_hash(pending_cnrs, pdf_dir)
    unique_pdfs = len(hash_to_cnrs)
    dup_cnrs = sum(len(v) - 1 for v in hash_to_cnrs.values())
    logger.info(
        "Missing PDFs=%d  unique_pdfs=%d  duplicate_cnrs_saved=%d",
        missing, unique_pdfs, dup_cnrs,
    )

    # Step 3 — build dedup map (primary → all siblings)
    # primary CNR = first alphabetically in each group
    dedup_map: dict[str, list[str]] = {}
    primary_to_pdf: dict[str, Path] = {}
    for h, cnrs in hash_to_cnrs.items():
        primary = sorted(cnrs)[0]
        dedup_map[primary] = cnrs
        pdf_path = pdf_dir / f"{primary}.pdf"
        primary_to_pdf[primary] = pdf_path

    dedup_map_path = output_dir / "dedup_map.json"
    dedup_map_path.write_text(json.dumps(dedup_map, indent=2), encoding="utf-8")
    logger.info("Saved dedup_map.json (%d primary PDFs)", len(dedup_map))

    # Step 4 — extract text + build JSONL
    jsonl_path = output_dir / "openai_batch_requests.jsonl"
    truncated_count = 0
    extraction_errors = 0
    written = 0

    with jsonl_path.open("w", encoding="utf-8") as out:
        for i, (primary, pdf_path) in enumerate(sorted(primary_to_pdf.items()), 1):
            if i % 1000 == 0:
                logger.info("  %d/%d processed...", i, len(primary_to_pdf))

            try:
                text = extract_text(pdf_path)
                if not text:
                    raise ValueError("empty text")
            except Exception as exc:
                logger.warning("Extraction error %s: %s", primary, exc)
                extraction_errors += 1
                continue

            text, was_truncated = truncate_tokens(text, enc)
            if was_truncated:
                truncated_count += 1

            req = build_request(primary, text, prompt)
            out.write(json.dumps(req, ensure_ascii=False) + "\n")
            written += 1

    logger.info(
        "Done — requests_written=%d  truncated=%d  extraction_errors=%d",
        written, truncated_count, extraction_errors,
    )
    logger.info("Output: %s", jsonl_path)
    logger.info(
        "File size: %.1f MB", jsonl_path.stat().st_size / 1_048_576
    )


if __name__ == "__main__":
    main()
