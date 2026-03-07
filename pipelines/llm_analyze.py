"""LLM-based case law analysis pipeline.

Reads PDFs, extracts text, calls an LLM with a structured prompt,
validates the response against a Pydantic schema, and saves results.

Output layout:
    {output_dir}/
        successes/{stem}.json          — validated analysis JSON
        failures/{stem}_{error}.log    — error details + raw LLM response

Usage (API):
    from pathlib import Path
    from pipelines.llm_providers import get_provider
    from pipelines.llm_analyze import run_analysis, discover_pdfs

    provider = get_provider("anthropic", model="claude-opus-4-6")
    pdfs = discover_pdfs(Path("/data/cases"))
    stats = run_analysis(pdfs, provider, output_dir=Path(".data/analysis"))

Usage (CLI):
    python -m pipelines.llm_analyze \\
        --input /data/cases/ \\
        --provider anthropic \\
        --model claude-opus-4-6 \\
        --output .data/analysis
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
from pydantic import BaseModel, ValidationError

from .llm_providers import PROVIDERS, LLMProvider, get_provider

logger = logging.getLogger(__name__)


# ── Pydantic output schema ─────────────────────────────────────────────────────


class CaseLawAnalysis(BaseModel):
    """Validated structured output from LLM analysis of a legal judgment.

    All fields are required strings. The PROMPT.md instructs the LLM to use
    "Not Mentioned" when information is absent, so missing or null fields
    are rejected.
    """

    summary: str
    facts: str
    issues: str
    petitioner_arguments: str
    respondent_arguments: str
    analysis_of_law: str
    precedent_analysis: str
    courts_reasoning: str
    conclusion: str
    ratio_decidendi: str

    model_config = {"extra": "ignore"}  # Tolerate extra fields the LLM may add


# ── Error type constants ───────────────────────────────────────────────────────

ERROR_EXTRACTION = "extraction_error"
ERROR_API = "api_error"
ERROR_PARSE = "parse_error"
ERROR_VALIDATION = "validation_error"


# ── Result types ───────────────────────────────────────────────────────────────


@dataclass
class AnalysisSuccess:
    pdf_path: Path
    analysis: CaseLawAnalysis
    raw_response: str


@dataclass
class AnalysisFailure:
    pdf_path: Path
    error_type: str  # One of the ERROR_* constants
    error_detail: str
    raw_response: str = ""


AnalysisResult = AnalysisSuccess | AnalysisFailure


# ── Text extraction ────────────────────────────────────────────────────────────


def extract_text(pdf_path: Path) -> str:
    """Extract all text from a PDF using PyMuPDF.

    Raises:
        FileNotFoundError: If the PDF path does not exist.
        ValueError:        If no text could be extracted (e.g. scanned image PDF).
        fitz.FileDataError: If the bytes are not a valid PDF.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    fitz.TOOLS.mupdf_display_errors(False)
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text() for page in doc]
    doc.close()

    text = "\n".join(pages).strip()
    if not text:
        raise ValueError(
            f"No text extracted from '{pdf_path.name}' — may be a scanned/image-only PDF"
        )
    return text


# ── JSON parsing ───────────────────────────────────────────────────────────────


def parse_json_response(raw: str) -> dict:
    """Parse a JSON string from an LLM response.

    Handles markdown code fences (```json ... ```) that some models wrap
    around their output even when instructed not to.

    Raises:
        json.JSONDecodeError: If the string is not valid JSON after stripping fences.
    """
    text = raw.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        # Scan from end to find the closing fence
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        # Skip the opening fence line (```json or ```)
        text = "\n".join(lines[1:end]).strip()

    return json.loads(text)


# ── Single-PDF analysis ────────────────────────────────────────────────────────


def analyze_pdf(pdf_path: Path, provider: LLMProvider) -> AnalysisResult:
    """Full analysis cycle for one PDF.

    Steps:
      1. Extract text with PyMuPDF
      2. Call the LLM provider
      3. Parse JSON (strip markdown fences if present)
      4. Validate against CaseLawAnalysis

    Returns AnalysisSuccess on the happy path, AnalysisFailure for any error.
    """
    # Step 1 — extract text
    try:
        text = extract_text(pdf_path)
    except Exception as exc:
        return AnalysisFailure(
            pdf_path=pdf_path,
            error_type=ERROR_EXTRACTION,
            error_detail=str(exc),
        )

    # Step 2 — call LLM
    raw_response = ""
    try:
        raw_response = provider.complete(text)
    except Exception as exc:
        return AnalysisFailure(
            pdf_path=pdf_path,
            error_type=ERROR_API,
            error_detail=str(exc),
            raw_response=raw_response,
        )

    # Step 3 — parse JSON
    try:
        data = parse_json_response(raw_response)
    except json.JSONDecodeError as exc:
        return AnalysisFailure(
            pdf_path=pdf_path,
            error_type=ERROR_PARSE,
            error_detail=str(exc),
            raw_response=raw_response,
        )

    # Some models wrap the object in a single-element array — unwrap it.
    if isinstance(data, list):
        if len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        else:
            return AnalysisFailure(
                pdf_path=pdf_path,
                error_type=ERROR_PARSE,
                error_detail=f"Expected a JSON object, got a list with {len(data)} item(s)",
                raw_response=raw_response,
            )

    # Step 4 — validate schema
    try:
        analysis = CaseLawAnalysis(**data)
    except ValidationError as exc:
        return AnalysisFailure(
            pdf_path=pdf_path,
            error_type=ERROR_VALIDATION,
            error_detail=exc.json(),
            raw_response=raw_response,
        )

    return AnalysisSuccess(
        pdf_path=pdf_path,
        analysis=analysis,
        raw_response=raw_response,
    )


# ── Output writers ─────────────────────────────────────────────────────────────


def write_success(result: AnalysisSuccess, output_dir: Path) -> Path:
    """Write the validated analysis to output_dir/{stem}.json."""
    out = output_dir / f"{result.pdf_path.stem}.json"
    out.write_text(result.analysis.model_dump_json(indent=2), encoding="utf-8")
    return out


def write_failure(result: AnalysisFailure, log_dir: Path) -> Path:
    """Write error details and the raw LLM response to log_dir/{stem}_{error_type}.log."""
    log = log_dir / f"{result.pdf_path.stem}_{result.error_type}.log"
    payload = {
        "pdf": str(result.pdf_path),
        "error_type": result.error_type,
        "error_detail": result.error_detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_response": result.raw_response,
    }
    log.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return log


# ── Run statistics ─────────────────────────────────────────────────────────────


@dataclass
class RunStats:
    total: int = 0
    succeeded: int = 0
    failed_extraction: int = 0
    failed_api: int = 0
    failed_parse: int = 0
    failed_validation: int = 0

    @property
    def failed(self) -> int:
        return (
            self.failed_extraction
            + self.failed_api
            + self.failed_parse
            + self.failed_validation
        )


# ── Pipeline entry points ──────────────────────────────────────────────────────


def discover_pdfs(input_path: Path) -> list[Path]:
    """Return sorted PDF paths from a directory or single PDF file.

    Raises:
        FileNotFoundError: If a given file path does not exist.
        ValueError:        If a directory has no PDFs, or the input is neither
                           a .pdf file nor a directory.
    """
    if input_path.is_dir():
        pdfs = sorted(input_path.rglob("*.pdf"))
        if not pdfs:
            raise ValueError(f"No PDF files found in directory: {input_path}")
        return pdfs

    if input_path.suffix.lower() == ".pdf":
        if not input_path.exists():
            raise FileNotFoundError(f"PDF not found: {input_path}")
        return [input_path]

    raise ValueError(
        f"Input must be a .pdf file or a directory, got: {input_path}"
    )


def run_analysis(
    pdf_paths: list[Path],
    provider: LLMProvider,
    output_dir: Path,
) -> RunStats:
    """Analyze a list of PDFs and write results under output_dir.

    Args:
        pdf_paths:  Ordered list of PDF paths to process.
        provider:   Configured LLM provider instance.
        output_dir: Root output directory.
                    Successes → output_dir/successes/
                    Failures  → output_dir/failures/

    Returns:
        RunStats with counts broken down by outcome category.
    """
    success_dir = output_dir / "successes"
    failure_dir = output_dir / "failures"
    success_dir.mkdir(parents=True, exist_ok=True)
    failure_dir.mkdir(parents=True, exist_ok=True)

    stats = RunStats(total=len(pdf_paths))

    for i, pdf_path in enumerate(pdf_paths, 1):
        logger.info("[%d/%d] %s  via %s", i, stats.total, pdf_path.name, provider.name)

        result = analyze_pdf(pdf_path, provider)

        if isinstance(result, AnalysisSuccess):
            out = write_success(result, success_dir)
            stats.succeeded += 1
            logger.info("  ✓ %s", out.name)
        else:
            log = write_failure(result, failure_dir)
            match result.error_type:
                case "extraction_error":
                    stats.failed_extraction += 1
                case "api_error":
                    stats.failed_api += 1
                case "parse_error":
                    stats.failed_parse += 1
                case "validation_error":
                    stats.failed_validation += 1
            logger.warning("  ✗ %s → %s", result.error_type, log.name)

    logger.info(
        "Done — %d/%d succeeded | %d failed "
        "(extract:%d  api:%d  parse:%d  valid:%d)",
        stats.succeeded,
        stats.total,
        stats.failed,
        stats.failed_extraction,
        stats.failed_api,
        stats.failed_parse,
        stats.failed_validation,
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────────


def _cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="python -m pipelines.llm_analyze",
        description="Analyze legal judgment PDFs using an LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze all PDFs in a directory using Claude (default model):
  python -m pipelines.llm_analyze --input /data/cases/ --provider anthropic

  # Analyze specific files with GPT-4o:
  python -m pipelines.llm_analyze --input a.pdf b.pdf --provider openai --model gpt-4o

  # Analyze with Gemini, custom output dir:
  python -m pipelines.llm_analyze --input /data/cases/ --provider google --output .data/gemini
""",
    )
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        metavar="PATH",
        help="One or more PDF files, or a single directory of PDFs",
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=sorted(PROVIDERS),
        help="LLM provider to use",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override (uses provider default if omitted)",
    )
    parser.add_argument(
        "--output",
        default=".data/llm_analysis",
        metavar="DIR",
        help="Output directory (default: .data/llm_analysis)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Max tokens for LLM response (default: 4096)",
    )

    args = parser.parse_args()

    # Resolve all PDF paths
    pdf_paths: list[Path] = []
    for raw in args.input:
        pdf_paths.extend(discover_pdfs(Path(raw)))

    logger.info("Found %d PDF(s) to analyze", len(pdf_paths))

    provider_kwargs: dict = {"max_tokens": args.max_tokens}
    if args.model:
        provider_kwargs["model"] = args.model

    provider = get_provider(args.provider, **provider_kwargs)
    logger.info("Provider: %s", provider.name)

    stats = run_analysis(
        pdf_paths=pdf_paths,
        provider=provider,
        output_dir=Path(args.output),
    )

    if stats.failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    _cli()
