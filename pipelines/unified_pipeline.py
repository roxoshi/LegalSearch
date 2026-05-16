"""
Unified Data Pipeline for LegalSearch — Shard → Filter → Export

This pipeline handles Steps 1-3 of the data flow:
  1. Load metadata from parquet files (SC and/or HC)
  2. Extract PDFs in parallel from zip/tar archives
  3. NER-based GST relevance filtering (two-stage: keyword → NER)
  [4. Optionally export filtered docs to disk for handoff to ETL]

Steps 4-6 (HTML conversion, embedding, DB insert) are now handled by
``etl/ingest.py``, which reads the LLM-produced JSON analysis files.
"""

import argparse
import json
import logging
import os
import shutil
import sys
import warnings
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import cpu_count
from pathlib import Path

# Preload pip-installed NVIDIA CUDA shared libs so CuPy can find them.
import ctypes as _ctypes
import glob as _glob

for _lib in _glob.glob(
    os.path.join(sys.prefix, "lib", "python*", "site-packages", "nvidia", "*", "lib", "*.so.*")
):
    # Skip libnvblas — it intercepts BLAS calls and crashes without a config file.
    if "libnvblas" in _lib:
        continue
    try:
        _ctypes.CDLL(_lib, mode=_ctypes.RTLD_GLOBAL)
    except OSError:
        pass

# Suppress noisy warnings before importing ML libraries
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore", message=".*position_ids.*")
warnings.filterwarnings("ignore", message=".*sequence length is longer than.*")
warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from pipelines.optimizations import (  # noqa: E402
    extract_text_pymupdf,
    two_stage_filter,
)
from pipelines.shard_hc import (  # noqa: E402
    load_hc_metadata,
    parallel_extract_from_tars,
)

# Setup path for imports
sys.path.append(str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("unified_pipeline")

# Reduce noise from other loggers
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("safetensors").setLevel(logging.WARNING)

# Constants
KEEP_COLUMNS = [
    "title",
    "petitioner",
    "respondent",
    "judge",
    "citation",
    "case_id",
    "cnr",
    "decision_date",
    "disposal_nature",
    "court",
    "nc_display",
    "year",
    "path",
]

GST_STATUTES_KEYWORDS = ["goods and services", "goods & services"]
MAX_TEXT_CHARS = 6000
NUM_WORKERS = max(4, cpu_count())


@dataclass
class DocumentData:
    """In-memory representation of a document during processing."""

    case_id: str
    title: str
    petitioner: str = "Unknown"
    respondent: str = "Unknown"
    judge: str = "Unknown"
    citation: str = "Unknown"
    court: str = "Unknown Court"
    decision_date: str = ""
    year: str = ""
    path: str = ""
    text_content: str = ""
    pdf_bytes: bytes | None = None
    pdf_staging_path: str | None = None
    is_gst_core: bool | None = None
    extracted_provisions: list[str] = field(default_factory=list)
    extracted_statutes: list[str] = field(default_factory=list)


def load_ner_model():
    """Load spaCy NER model and optimize for inference speed."""
    import spacy

    try:
        import torch

        if torch.cuda.is_available():
            try:
                spacy.require_gpu()
                logger.info("GPU enabled for spaCy")
            except Exception:
                pass
        else:
            cpu_threads = os.cpu_count() or 4
            torch.set_num_threads(cpu_threads)
            torch.set_num_interop_threads(min(4, cpu_threads))
            logger.info(f"PyTorch CPU threads: {cpu_threads} intra-op, {min(4, cpu_threads)} inter-op")
    except ImportError:
        pass

    nlp = None

    local_model_dir = str(Path(__file__).resolve().parent.parent / ".data" / "models" / "en_legal_ner_trf")
    model_paths = [
        local_model_dir,
        "/app/models/en_legal_ner_trf",
        "en_legal_ner_trf",
    ]

    for path in model_paths:
        try:
            nlp = spacy.load(path)
            logger.info(f"Loaded NER model from: {path}")
            break
        except OSError:
            continue

    if nlp is None:
        raise RuntimeError(
            "Could not load NER model en_legal_ner_trf from any source.\n"
            f"Searched: {model_paths}\n"
            "To download the model locally, run:\n"
            "  uv run python scripts/install_spacy_model.py"
        )

    required_pipes = {"transformer", "ner"}
    unused_pipes = [name for name in nlp.pipe_names if name not in required_pipes]
    if unused_pipes:
        nlp.select_pipes(enable=list(required_pipes & set(nlp.pipe_names)))
        logger.info(f"Disabled unused pipes: {unused_pipes} — active: {nlp.pipe_names}")
    else:
        logger.info(f"Active pipes: {nlp.pipe_names}")

    return nlp


def batch_ner_filter(
    docs: list[DocumentData],
    nlp,
    negatives_path: str = ".data/logs/classifier_negatives.jsonl",
) -> list[DocumentData]:
    """Two-stage document filtering: keyword pre-filter → batch NER inference.

    Stage 2 rejects (passed keyword filter but failed NER) are written to
    negatives_path as JSONL for use as training data for the GST classifier.
    """
    logger.info(f"Starting two-stage filter on {len(docs)} documents...")

    filtered, stats = two_stage_filter(
        docs, nlp, text_getter=lambda d: d.text_content[:MAX_TEXT_CHARS], max_chars=MAX_TEXT_CHARS
    )

    logger.info(
        f"Stage 1 (Keywords): {stats['passed_keyword_filter']}/{stats['total']} passed "
        f"({stats['rejected_by_keyword']} filtered out - {stats['keyword_filter_rate'] * 100:.1f}% reduction)"
    )
    logger.info(
        f"Stage 2 (NER): {stats['passed_ner']}/{stats['passed_keyword_filter']} are GST-relevant"
    )
    logger.info(f"Final: {len(filtered)}/{len(docs)} documents selected")

    keyword_rejects: list[DocumentData] = stats.get("keyword_rejects", [])
    if keyword_rejects:
        Path(negatives_path).parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with open(negatives_path, "a", encoding="utf-8") as f:
            for doc in keyword_rejects:
                record = {
                    "case_id": doc.case_id,
                    "label": 0,
                    "text": doc.text_content[:MAX_TEXT_CHARS],
                    "court": doc.court,
                    "year": doc.year,
                    "decision_date": doc.decision_date,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
        logger.info(f"Saved {written} keyword rejects (clean negatives) to {negatives_path}")

    return filtered


def persist_filtered_pdfs(docs: list[DocumentData], output_dir: str) -> int:
    """Copy passing PDFs to a persistent directory for future use.

    Args:
        docs: Filtered DocumentData list (after NER Step 3)
        output_dir: Target directory (e.g. `.data/gst_pdfs`)

    Returns:
        Number of PDFs persisted
    """
    os.makedirs(output_dir, exist_ok=True)
    count = 0

    for doc in docs:
        safe_id = _sanitize_filename(doc.case_id)
        dest = os.path.join(output_dir, f"{safe_id}.pdf")

        if doc.pdf_staging_path and Path(doc.pdf_staging_path).exists():
            shutil.copy2(doc.pdf_staging_path, dest)
            count += 1
        elif doc.pdf_bytes:
            Path(dest).write_bytes(doc.pdf_bytes)
            count += 1

    logger.info(f"Persisted {count} filtered PDFs to {output_dir}")
    return count


def _sanitize_filename(case_id: str) -> str:
    """Sanitize case_id for use as a filename."""
    return case_id.replace("/", "_").replace("\\", "_")


def export_after_filter(docs: list[DocumentData], export_dir: str) -> int:
    """Export filtered documents to disk for two-stage pipeline handoff.

    Writes metadata to JSONL and PDFs to a flat directory. HC docs are moved
    from staging (not copied) to avoid doubling disk usage. SC docs write
    pdf_bytes to disk.

    Args:
        docs: Filtered DocumentData list (after NER Step 3)
        export_dir: Target directory (e.g. `.data/export`)

    Returns:
        Number of documents exported
    """
    export_path = Path(export_dir)
    pdfs_path = export_path / "pdfs"
    pdfs_path.mkdir(parents=True, exist_ok=True)

    jsonl_path = export_path / "export.jsonl"
    count = 0

    with open(jsonl_path, "w") as f:
        for doc in docs:
            safe_id = _sanitize_filename(doc.case_id)
            pdf_filename: str | None = None

            if doc.pdf_staging_path and Path(doc.pdf_staging_path).exists():
                pdf_filename = f"{safe_id}.pdf"
                shutil.move(doc.pdf_staging_path, str(pdfs_path / pdf_filename))
            elif doc.pdf_bytes:
                pdf_filename = f"{safe_id}.pdf"
                (pdfs_path / pdf_filename).write_bytes(doc.pdf_bytes)

            record = {
                "case_id": doc.case_id,
                "title": doc.title,
                "petitioner": doc.petitioner,
                "respondent": doc.respondent,
                "judge": doc.judge,
                "citation": doc.citation,
                "court": doc.court,
                "decision_date": doc.decision_date,
                "year": doc.year,
                "path": doc.path,
                "text_content": doc.text_content,
                "is_gst_core": doc.is_gst_core,
                "extracted_provisions": doc.extracted_provisions,
                "extracted_statutes": doc.extracted_statutes,
                "pdf_filename": pdf_filename,
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    logger.info(f"Exported {count} documents to {export_dir}")
    return count


def import_filtered_docs(import_dir: str) -> list[DocumentData]:
    """Import previously exported documents from disk.

    Args:
        import_dir: Directory containing export.jsonl and pdfs/

    Returns:
        List of DocumentData with pdf_staging_path set

    Raises:
        FileNotFoundError: If export.jsonl is missing
    """
    import_path = Path(import_dir)
    jsonl_path = import_path / "export.jsonl"

    if not jsonl_path.exists():
        raise FileNotFoundError(f"Export manifest not found: {jsonl_path}")

    docs = []
    with open(jsonl_path) as f:
        for line in f:
            record = json.loads(line)

            pdf_staging_path: str | None = None
            if record.get("pdf_filename"):
                candidate = import_path / "pdfs" / record["pdf_filename"]
                if candidate.exists():
                    pdf_staging_path = str(candidate)

            doc = DocumentData(
                case_id=record["case_id"],
                title=record.get("title", ""),
                petitioner=record.get("petitioner", "Unknown"),
                respondent=record.get("respondent", "Unknown"),
                judge=record.get("judge", "Unknown"),
                citation=record.get("citation", "Unknown"),
                court=record.get("court", "Unknown Court"),
                decision_date=record.get("decision_date", ""),
                year=record.get("year", ""),
                path=record.get("path", ""),
                text_content=record.get("text_content", ""),
                pdf_staging_path=pdf_staging_path,
                is_gst_core=record.get("is_gst_core"),
                extracted_provisions=record.get("extracted_provisions", []),
                extracted_statutes=record.get("extracted_statutes", []),
            )
            docs.append(doc)

    logger.info(f"Imported {len(docs)} documents from {import_dir}")
    return docs


def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Extract text from PDF bytes using PyMuPDF (10-50x faster than pdfplumber)."""
    return extract_text_pymupdf(pdf_bytes, max_chars)


def _extract_single_record(args) -> DocumentData | None:
    """Extract a single record - for parallel processing."""
    record, judgments_dir = args
    cite = record.get("citation")
    year = str(record.get("year", ""))

    if not cite:
        return None

    zip_path = os.path.join(judgments_dir, f"SC-GST-{year}.zip")
    if not os.path.exists(zip_path):
        return None

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            all_files = zf.namelist()
            filename = os.path.basename(record.get("path", ""))

            target = next((f for f in all_files if f.lower().endswith(filename.lower())), None)
            if not target:
                base = filename.rsplit(".", 1)[0]
                en_filename = f"{base}_EN.pdf"
                target = next(
                    (f for f in all_files if f.lower().endswith(en_filename.lower())), None
                )

            if not target:
                return None

            pdf_bytes = zf.read(target)
            text_content = extract_text_from_pdf_bytes(pdf_bytes)

            return DocumentData(
                case_id=record.get("case_id", ""),
                title=record.get("title", ""),
                petitioner=record.get("petitioner", "Unknown"),
                respondent=record.get("respondent", "Unknown"),
                judge=record.get("judge", "Unknown"),
                citation=cite,
                court=record.get("court", "Unknown Court"),
                decision_date=record.get("decision_date", ""),
                year=year,
                path=record.get("path", ""),
                text_content=text_content,
                pdf_bytes=pdf_bytes,
            )
    except Exception:
        return None


def parallel_extract_from_zips(records: list[dict], judgments_dir: str) -> list[DocumentData]:
    """Extract PDFs in parallel using ProcessPoolExecutor."""
    logger.info(f"Extracting {len(records)} PDFs in parallel ({NUM_WORKERS} workers)...")

    args_list = [(r, judgments_dir) for r in records]

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        results = list(
            tqdm(
                executor.map(_extract_single_record, args_list, chunksize=50),
                total=len(args_list),
                desc="PDF Extraction",
                unit="pdf",
            )
        )

    docs = [d for d in results if d is not None]
    logger.info(f"Extracted {len(docs)}/{len(records)} documents")
    return docs


def load_sc_metadata(metadata_dir: str) -> list[dict]:
    """Load and deduplicate SC metadata from parquet files."""
    files = [
        f for f in os.listdir(metadata_dir) if f.startswith("SC-GST-") and f.endswith(".parquet")
    ]

    all_records = []
    for file in files:
        df = pd.read_parquet(os.path.join(metadata_dir, file), columns=KEEP_COLUMNS)
        all_records.extend(df.to_dict(orient="records"))

    seen = set()
    unique = []
    for record in all_records:
        cite = record.get("citation")
        if cite and cite not in seen:
            seen.add(cite)
            unique.append(record)

    logger.info(f"Loaded {len(unique)} unique SC records (from {len(all_records)} total)")
    return unique


# Backward-compatible alias
load_metadata = load_sc_metadata


def run_pipeline(
    metadata_dir: str,
    judgments_dir: str,
    limit: int = 0,
    skip_filter: bool = False,
    court_type: str = "all",
    years: list[int] | None = None,
    export_after_filter_dir: str | None = None,
    import_filtered_dir: str | None = None,
) -> int:
    """Run the pipeline: shard → filter → persist PDFs [→ export].

    Args:
        metadata_dir: Directory containing parquet metadata files
        judgments_dir: Directory containing judgment zip/tar files
        limit: Limit number of records (0 = no limit)
        skip_filter: Skip NER filtering
        court_type: Which courts to process - "sc", "hc", or "all"
        years: Optional list of years to filter (applies to HC loading)
        export_after_filter_dir: If set, export filtered docs to this dir and stop
        import_filtered_dir: If set, import docs from this dir (skip Steps 1-3)
    """
    # === IMPORT MODE: Skip Steps 1-3, load from previously exported data ===
    if import_filtered_dir:
        logger.info("=" * 50)
        logger.info(f"IMPORT MODE: Loading filtered docs from {import_filtered_dir}")
        logger.info("=" * 50)
        docs = import_filtered_docs(import_filtered_dir)
        if not docs:
            logger.error("No documents found in import directory!")
            return 0
        persist_filtered_pdfs(docs, ".data/gst_pdfs")
        return len(docs)

    # === STEP 1: Load metadata ===
    logger.info("=" * 50)
    logger.info(f"STEP 1: Loading metadata (court_type={court_type})")
    logger.info("=" * 50)

    sc_records: list[dict] = []
    hc_records: list[dict] = []

    if court_type in ("sc", "all"):
        sc_records = load_sc_metadata(metadata_dir)
    if court_type in ("hc", "all"):
        hc_records = load_hc_metadata(metadata_dir, years=years)

    total_records = len(sc_records) + len(hc_records)
    logger.info(f"Total records: {total_records} (SC: {len(sc_records)}, HC: {len(hc_records)})")

    if limit > 0:
        sc_records = sc_records[:limit]
        hc_records = hc_records[: max(0, limit - len(sc_records))]
        logger.info(f"Limited to {len(sc_records) + len(hc_records)} records")

    # === STEP 2: Parallel PDF extraction ===
    logger.info("=" * 50)
    logger.info("STEP 2: Extracting PDFs (parallel)")
    logger.info("=" * 50)

    docs = []
    if sc_records:
        logger.info(f"Extracting {len(sc_records)} SC documents from zips...")
        docs.extend(parallel_extract_from_zips(sc_records, judgments_dir))
    if hc_records:
        logger.info(f"Extracting {len(hc_records)} HC documents from tars...")
        docs.extend(parallel_extract_from_tars(hc_records, judgments_dir))

    if not docs:
        logger.error("No documents extracted!")
        return 0

    # === STEP 3: NER Filter (batch processing) ===
    if not skip_filter:
        logger.info("=" * 50)
        logger.info("STEP 3: NER Filtering (two-stage)")
        logger.info("=" * 50)
        nlp = load_ner_model()
        docs = batch_ner_filter(docs, nlp)
        del nlp  # Free memory

        persist_filtered_pdfs(docs, ".data/gst_pdfs")

        if not docs:
            logger.error("No GST-relevant documents found!")
            return 0
    else:
        logger.info("STEP 3: Skipping NER filter")
        for doc in docs:
            doc.is_gst_core = True
        persist_filtered_pdfs(docs, ".data/gst_pdfs")

    # === EXPORT MODE: Save filtered docs and stop ===
    if export_after_filter_dir:
        logger.info("=" * 50)
        logger.info(f"EXPORT MODE: Saving filtered docs to {export_after_filter_dir}")
        logger.info("=" * 50)
        count = export_after_filter(docs, export_after_filter_dir)
        logger.info(f"Exported {count} documents. Pipeline stopping after filter stage.")
        return count

    logger.info("=" * 50)
    logger.info(f"PIPELINE COMPLETE: {len(docs)} documents filtered and persisted to .data/gst_pdfs")
    logger.info("Next step: run etl/ingest.py with the LLM analysis JSONs to load into the database.")
    logger.info("=" * 50)

    return len(docs)


def main():
    parser = argparse.ArgumentParser(
        description="LegalSearch pipeline: shard → NER filter → persist PDFs"
    )
    parser.add_argument("--metadata-dir", default=".data/metadata/raw")
    parser.add_argument("--judgments-dir", default=".data/GST_judgments")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-filter", action="store_true", help="Skip NER filtering")
    parser.add_argument(
        "--court-type",
        choices=["sc", "hc", "all"],
        default="all",
        help="Which court data to process: sc (Supreme Court), hc (High Court), or all",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help="Filter HC data to specific years (e.g. --years 2018 2019)",
    )

    # Two-stage pipeline arguments
    parser.add_argument(
        "--export-after-filter",
        action="store_true",
        help="Run Steps 1-3 (extract + filter), export results to disk, then stop",
    )
    parser.add_argument(
        "--export-dir",
        default=".data/export",
        help="Directory for exported filtered docs (default: .data/export)",
    )
    parser.add_argument(
        "--import-filtered",
        action="store_true",
        help="Skip Steps 1-3, import previously exported docs, persist PDFs",
    )
    parser.add_argument(
        "--import-dir",
        default=".data/export",
        help="Directory to import filtered docs from (default: .data/export)",
    )

    args = parser.parse_args()

    if args.export_after_filter and args.import_filtered:
        parser.error("Cannot use --export-after-filter and --import-filtered together")

    run_pipeline(
        metadata_dir=args.metadata_dir,
        judgments_dir=args.judgments_dir,
        limit=args.limit,
        skip_filter=args.skip_filter,
        court_type=args.court_type,
        years=args.years,
        export_after_filter_dir=args.export_dir if args.export_after_filter else None,
        import_filtered_dir=args.import_dir if args.import_filtered else None,
    )


if __name__ == "__main__":
    main()
