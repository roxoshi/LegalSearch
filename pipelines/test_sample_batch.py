"""Run a sample of 20 docs through GPT-4o-mini and report token usage + cost.

Usage:
    set -a && source envs/.env.staging && set +a
    uv run python -m pipelines.test_sample_batch
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import fitz
import tiktoken
from openai import OpenAI
from pydantic import ValidationError

from pipelines.llm_analyze import CaseLawAnalysis, parse_json_response
from pipelines.llm_providers import load_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_sample")

SAMPLE_CNRS = [
    "HCMA011885212006",
    "CGHC010135382010",
    "ODHC010369562017",
    "JHHC010174572018",
    "KLHC010349142018",
    "HPHC010085902018",
    "ODHC010014582019",
    "KLHC010509052018",
    "HCMA010180052016",
    "GJHC240208052019",
    "JHHC010187122019",
    "HBHC010230602020",
    "HCMD010435832021",
    "HCMA010147502015",
    "UPHC011567742021",
    "APHC010467662022",
    "APHC010243882020",
    "RJHC010820582019",
    "BRHC010097202023",
    "WBCHCA0374112023",
]

PDF_DIR = Path(".data/gst_pdfs")
OUTPUT_DIR = Path(".data/test_analysis")
MODEL = "gpt-4o-mini"
INPUT_PRICE_PER_M = 0.075   # batch API pricing
OUTPUT_PRICE_PER_M = 0.30

TOKEN_LIMIT = 128_000
HEAD_TOKENS = 60_000
TAIL_TOKENS = 68_000


def extract_and_truncate(pdf_path: Path, enc) -> tuple[str, bool]:
    fitz.TOOLS.mupdf_display_errors(False)
    doc = fitz.open(str(pdf_path))
    text = "\n".join(page.get_text() for page in doc).strip()
    doc.close()
    tokens = enc.encode(text, disallowed_special=())
    if len(tokens) <= TOKEN_LIMIT:
        return text, False
    head = tokens[:HEAD_TOKENS]
    tail = tokens[-TAIL_TOKENS:]
    truncated = enc.decode(head) + "\n\n[... middle omitted ...]\n\n" + enc.decode(tail)
    return truncated, True


def main() -> None:
    client = OpenAI()
    enc = tiktoken.get_encoding("cl100k_base")
    prompt = load_prompt()

    success_dir = OUTPUT_DIR / "successes"
    failure_dir = OUTPUT_DIR / "failures"
    success_dir.mkdir(parents=True, exist_ok=True)
    failure_dir.mkdir(parents=True, exist_ok=True)

    total_input_tokens = 0
    total_output_tokens = 0
    succeeded = 0
    failed = 0

    for i, cnr in enumerate(SAMPLE_CNRS, 1):
        pdf_path = PDF_DIR / f"{cnr}.pdf"
        if not pdf_path.exists():
            logger.warning("[%d/20] %s — PDF not found, skipping", i, cnr)
            failed += 1
            continue

        logger.info("[%d/20] %s", i, cnr)

        try:
            text, truncated = extract_and_truncate(pdf_path, enc)
            if truncated:
                logger.info("  (truncated to 128K)")
        except Exception as exc:
            logger.warning("  Extraction error: %s", exc)
            failed += 1
            continue

        message = f"{prompt}\n\n# CASE LAW TEXT\n\n{text}"

        try:
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=2000,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": message}],
            )
        except Exception as exc:
            logger.warning("  API error: %s", exc)
            failed += 1
            continue

        usage = response.usage
        in_tok = usage.prompt_tokens
        out_tok = usage.completion_tokens
        total_input_tokens += in_tok
        total_output_tokens += out_tok

        raw = response.choices[0].message.content
        try:
            data = parse_json_response(raw)
            analysis = CaseLawAnalysis(**data)
            out_path = success_dir / f"{cnr}.json"
            out_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
            logger.info("  ✓  in=%d  out=%d  tokens", in_tok, out_tok)
            succeeded += 1
        except (json.JSONDecodeError, ValidationError) as exc:
            (failure_dir / f"{cnr}_error.log").write_text(
                json.dumps({"error": str(exc), "raw": raw}, indent=2)
            )
            logger.warning("  ✗  parse/validation error: %s", exc)
            failed += 1

    # Cost summary
    input_cost = total_input_tokens * INPUT_PRICE_PER_M / 1_000_000
    output_cost = total_output_tokens * OUTPUT_PRICE_PER_M / 1_000_000
    total_cost = input_cost + output_cost

    print("\n" + "=" * 50)
    print(f"Results:  {succeeded} succeeded  |  {failed} failed")
    print(f"Input tokens:   {total_input_tokens:,}  (${input_cost:.4f})")
    print(f"Output tokens:  {total_output_tokens:,}  (${output_cost:.4f})")
    print(f"Total cost:     ${total_cost:.4f}")
    print(f"Avg cost/doc:   ${total_cost/max(succeeded,1):.4f}")
    print(f"Projected 42K:  ${total_cost/max(succeeded,1)*42213:.2f}")
    print("=" * 50)
    print(f"\nOutput: {success_dir}")


if __name__ == "__main__":
    main()
