"""
Unified Optimized Data Pipeline for LegalSearch - V3 (TIER 1 OPTIMIZATIONS)

Optimizations:
1. Two-stage keyword pre-filter (skips 80%+ of NER inference)
2. PyMuPDF text extraction (10-50x faster than pdfplumber)
3. Batch NER inference using spaCy's nlp.pipe() (5-10x faster)
4. Parallel PDF extraction with multiprocessing
5. Parallel PDF to HTML conversion (more workers)
6. Larger embedding batch sizes
7. Process all records in one go (no small batches)
"""

import argparse
import json
import logging
import os
import shutil
import sys
import warnings
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import cpu_count
from pathlib import Path
from typing import Any

# Preload pip-installed NVIDIA CUDA shared libs so CuPy can find them.
# PyTorch handles this internally, but CuPy uses dlopen which needs the libs
# visible at process level. Without this, spaCy GPU support silently fails.
import ctypes as _ctypes
import glob as _glob

for _lib in _glob.glob(
    os.path.join(sys.prefix, "lib", "python*", "site-packages", "nvidia", "*", "lib", "*.so.*")
):
    # Skip libnvblas — it intercepts BLAS calls and crashes without a config file.
    # PyTorch and spaCy use cuBLAS directly and don't need the NVBLAS shim.
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

# Import optimizations
from pipelines.optimizations import (  # noqa: E402
    ONNXEmbedder,
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
logging.getLogger("app.embeddings").setLevel(logging.WARNING)
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
EMBEDDING_BATCH_SIZE = 1024  # Increased for speed
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
    display_html: str | None = None
    is_gst_core: bool | None = None
    extracted_provisions: list[str] = field(default_factory=list)
    extracted_statutes: list[str] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    embeddings: list[list[float]] = field(default_factory=list)


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
            # Maximize CPU parallelism for PyTorch ops (matrix multiply, etc.)
            # By default PyTorch may only use 1-2 threads.
            cpu_threads = os.cpu_count() or 4
            torch.set_num_threads(cpu_threads)
            torch.set_num_interop_threads(min(4, cpu_threads))
            logger.info(f"PyTorch CPU threads: {cpu_threads} intra-op, {min(4, cpu_threads)} inter-op")
    except ImportError:
        pass

    nlp = None

    # Try loading from multiple paths: local dev, Docker, then package name
    local_model_dir = str(Path(__file__).resolve().parent.parent / ".data" / "models" / "en_legal_ner_trf")
    model_paths = [
        local_model_dir,                   # Local dev path (.data/models/)
        "/app/models/en_legal_ner_trf",    # Docker path
        "en_legal_ner_trf",                # Installed package name
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

    # Disable pipe components we don't need — only transformer + NER are
    # required. Other pipes (sentencizer, tok2vec, tagger, parser, etc.)
    # add overhead per document without contributing to entity extraction.
    required_pipes = {"transformer", "ner"}
    unused_pipes = [name for name in nlp.pipe_names if name not in required_pipes]
    if unused_pipes:
        nlp.select_pipes(enable=list(required_pipes & set(nlp.pipe_names)))
        logger.info(f"Disabled unused pipes: {unused_pipes} — active: {nlp.pipe_names}")
    else:
        logger.info(f"Active pipes: {nlp.pipe_names}")

    return nlp


def batch_ner_filter(docs: list[DocumentData], nlp) -> list[DocumentData]:
    """
    Two-stage document filtering:
    1. Fast keyword pre-filter (skips 80%+ of non-GST docs)
    2. Batch NER inference only on candidates

    This is 5-10x faster than processing all documents with NER.
    """
    logger.info(f"Starting two-stage filter on {len(docs)} documents...")

    # Use the optimized two-stage filter
    filtered, stats = two_stage_filter(
        docs, nlp, text_getter=lambda d: d.text_content[:MAX_TEXT_CHARS], max_chars=MAX_TEXT_CHARS
    )

    # Log statistics
    logger.info(
        f"Stage 1 (Keywords): {stats['passed_keyword_filter']}/{stats['total']} passed "
        f"({stats['rejected_by_keyword']} filtered out - {stats['keyword_filter_rate'] * 100:.1f}% reduction)"
    )
    logger.info(
        f"Stage 2 (NER): {stats['passed_ner']}/{stats['passed_keyword_filter']} are GST-relevant"
    )
    logger.info(f"Final: {len(filtered)}/{len(docs)} documents selected")

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

            # Save PDF: move from HC staging or write SC bytes
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

    Reads the JSONL manifest and reconstructs DocumentData objects. PDFs are
    referenced via `pdf_staging_path` so that `_convert_single_pdf` can read
    and delete them (the export is consumed).

    Args:
        import_dir: Directory containing export.jsonl and pdfs/

    Returns:
        List of DocumentData ready for Steps 4-6

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

            # Find target file
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

    docs = []
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


def _convert_single_pdf(doc: DocumentData) -> DocumentData:
    """Convert single PDF to HTML.

    Reads from pdf_staging_path (disk) if available, otherwise falls back
    to pdf_bytes (in-memory). Frees both after conversion.
    """
    import tempfile

    from pipelines.convert_pdf import convert_pdf_to_html

    pdf_path: str | None = None
    temp_path: str | None = None

    try:
        if doc.pdf_staging_path and Path(doc.pdf_staging_path).exists():
            # Read from staging file (memory-efficient path)
            pdf_path = doc.pdf_staging_path
        elif doc.pdf_bytes:
            # Fallback: write in-memory bytes to temp file (SC path)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(doc.pdf_bytes)
                temp_path = f.name
            pdf_path = temp_path

        if pdf_path:
            doc.display_html = convert_pdf_to_html(Path(pdf_path))
        else:
            doc.display_html = f"<p>{doc.text_content}</p>"
    except Exception:
        doc.display_html = f"<p>{doc.text_content}</p>"
    finally:
        # Clean up staging file
        if doc.pdf_staging_path:
            Path(doc.pdf_staging_path).unlink(missing_ok=True)
            doc.pdf_staging_path = None
        # Clean up temp file (SC fallback)
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
        doc.pdf_bytes = None  # Free memory

    return doc


def parallel_convert_pdfs(docs: list[DocumentData]) -> list[DocumentData]:
    """Convert PDFs to HTML in parallel."""
    logger.info(f"Converting {len(docs)} PDFs to HTML ({NUM_WORKERS} workers)...")

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        results = list(
            tqdm(
                executor.map(_convert_single_pdf, docs),
                total=len(docs),
                desc="PDF to HTML",
                unit="doc",
            )
        )

    return results


def batch_chunk_and_embed(docs: list[DocumentData], model, splitter) -> list[DocumentData]:
    """Chunk and embed all documents with large batch sizes."""

    # Phase 1: Chunk all documents
    logger.info("Chunking documents...")
    all_chunks = []
    chunk_counts = []

    for doc in tqdm(docs, desc="Chunking", unit="doc"):
        if doc.text_content:
            chunks = splitter.split_text(doc.text_content)
            doc.chunks = chunks
            chunk_counts.append(len(chunks))
            all_chunks.extend(chunks)
        else:
            doc.chunks = []
            chunk_counts.append(0)

    total_chunks = len(all_chunks)
    logger.info(f"Total chunks: {total_chunks}")

    if not all_chunks:
        return docs

    # Phase 2: Batch embed - use large batch size for speed
    logger.info(
        f"Generating embeddings for {total_chunks} chunks (batch size: {EMBEDDING_BATCH_SIZE})..."
    )

    all_embeddings = []
    for i in tqdm(range(0, total_chunks, EMBEDDING_BATCH_SIZE), desc="Embeddings", unit="batch"):
        batch = all_chunks[i : i + EMBEDDING_BATCH_SIZE]
        batch_embeddings = model.encode(batch, show_progress_bar=False, batch_size=128)
        # Handle both numpy arrays (EmbeddingModel) and lists (ONNXEmbedder)
        if hasattr(batch_embeddings, "tolist"):
            all_embeddings.extend(batch_embeddings.tolist())
        else:
            all_embeddings.extend(batch_embeddings)

    # Distribute embeddings back
    idx = 0
    for doc, count in zip(docs, chunk_counts):
        doc.embeddings = all_embeddings[idx : idx + count]
        idx += count

    return docs


def bulk_insert_to_db(docs: list[DocumentData], db_url: str):
    """Insert all documents to database using bulk operations."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    try:
        from backend.app.models import Base, Document, DocumentChunk
    except ImportError:
        from app.models import Base, Document, DocumentChunk  # type: ignore[no-redef]

    logger.info(f"Inserting {len(docs)} documents to database...")

    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with SessionLocal() as db:
        # Clear existing if needed
        existing_ids = {r[0] for r in db.execute(text("SELECT case_id FROM documents")).fetchall()}
        case_ids = [doc.case_id for doc in docs]
        overlap = set(case_ids) & existing_ids

        if overlap:
            logger.info(f"Removing {len(overlap)} existing documents...")
            db.execute(
                text(
                    "DELETE FROM document_chunks WHERE document_id IN (SELECT id FROM documents WHERE case_id = ANY(:ids))"
                ),
                {"ids": list(overlap)},
            )
            db.execute(
                text("DELETE FROM documents WHERE case_id = ANY(:ids)"), {"ids": list(overlap)}
            )
            db.commit()

        # Bulk insert documents
        logger.info("Inserting documents...")
        db_docs = []
        for doc in tqdm(docs, desc="Preparing docs", unit="doc"):
            db_docs.append(
                Document(
                    title=doc.title,
                    petitioner=doc.petitioner,
                    respondent=doc.respondent,
                    judge=doc.judge,
                    citation=doc.citation,
                    decision_date=doc.decision_date,
                    court=doc.court,
                    case_id=doc.case_id,
                    content=doc.text_content,
                    display_content=doc.display_html,
                    is_gst_core=doc.is_gst_core,
                    extracted_provisions=doc.extracted_provisions,
                    extracted_statutes=doc.extracted_statutes,
                )
            )

        db.bulk_save_objects(db_docs, return_defaults=True)
        db.flush()

        # Get IDs
        doc_id_map = {
            r[0]: r[1]
            for r in db.execute(
                text("SELECT case_id, id FROM documents WHERE case_id = ANY(:ids)"),
                {"ids": case_ids},
            ).fetchall()
        }

        # Bulk insert chunks
        logger.info("Inserting chunks...")
        db_chunks = []
        for doc in tqdm(docs, desc="Preparing chunks", unit="doc"):
            doc_id = doc_id_map.get(doc.case_id)
            if doc_id:
                for chunk_text, embedding in zip(doc.chunks, doc.embeddings):
                    db_chunks.append(
                        DocumentChunk(
                            document_id=doc_id, chunk_content=chunk_text, embedding=embedding
                        )
                    )

        logger.info(f"Inserting {len(db_chunks)} chunks...")

        # Insert chunks in batches to avoid memory issues
        batch_size = 5000
        for i in tqdm(range(0, len(db_chunks), batch_size), desc="Chunk batches", unit="batch"):
            batch = db_chunks[i : i + batch_size]
            db.bulk_save_objects(batch)
            db.flush()

        db.commit()

    logger.info("Database insert complete")


def load_sc_metadata(metadata_dir: str) -> list[dict]:
    """Load and deduplicate SC metadata from parquet files."""
    files = [
        f for f in os.listdir(metadata_dir) if f.startswith("SC-GST-") and f.endswith(".parquet")
    ]

    all_records = []
    for file in files:
        df = pd.read_parquet(os.path.join(metadata_dir, file), columns=KEEP_COLUMNS)
        all_records.extend(df.to_dict(orient="records"))

    # Deduplicate by citation
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


def _load_env_file() -> dict[str, str]:
    """Load key=value pairs from the staging env file (no dependencies needed)."""
    project_root = Path(__file__).resolve().parent.parent
    env = os.getenv("ENVIRONMENT", "staging")
    env_path = project_root / "envs" / f".env.{env}"
    if not env_path.exists():
        env_path = project_root / "envs" / ".env.staging"
    if not env_path.exists():
        return {}

    vals: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Strip surrounding quotes
        value = value.strip().strip("'\"")
        # Skip placeholders
        if value.startswith("<") or value.startswith("${"):
            continue
        vals[key.strip()] = value
    return vals


def _build_db_url_from_env() -> str:
    """Construct DATABASE_URL from POSTGRES_* env vars, falling back to the env file."""
    from urllib.parse import quote_plus

    env_file = _load_env_file()

    def _get(key: str, default: str = "") -> str:
        return os.getenv(key) or env_file.get(key, default)

    user = _get("POSTGRES_USER")
    password = _get("POSTGRES_PASSWORD")
    db = _get("POSTGRES_DB", "search_db")
    port = _get("DB_PORT", "5432")

    if not user or not password:
        raise RuntimeError(
            "Cannot build DATABASE_URL: POSTGRES_USER and POSTGRES_PASSWORD are required.\n"
            "Either:\n"
            "  1. Set DATABASE_URL directly, or\n"
            "  2. Source your env file: set -a; source envs/.env.staging; set +a\n"
            "  3. Ensure envs/.env.staging exists with POSTGRES_USER and POSTGRES_PASSWORD set"
        )

    url = f"postgresql://{quote_plus(user)}:{quote_plus(password)}@localhost:{port}/{db}"
    logger.info(f"Constructed DATABASE_URL from env vars (db={db}, port={port})")
    return url


def run_pipeline(
    metadata_dir: str,
    judgments_dir: str,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    db_url: str | None = None,
    limit: int = 0,
    skip_filter: bool = False,
    use_onnx: bool = False,
    court_type: str = "all",
    years: list[int] | None = None,
    export_after_filter_dir: str | None = None,
    import_filtered_dir: str | None = None,
) -> int:
    """Run the optimized pipeline - processes everything in one go.

    Args:
        metadata_dir: Directory containing parquet metadata files
        judgments_dir: Directory containing judgment zip/tar files
        model_name: Embedding model name
        db_url: Database connection URL
        limit: Limit number of records (0 = no limit)
        skip_filter: Skip NER filtering
        use_onnx: Use ONNX Runtime for embeddings (2-4x faster on CPU)
        court_type: Which courts to process - "sc", "hc", or "all"
        years: Optional list of years to filter (applies to HC loading)
        export_after_filter_dir: If set, export filtered docs to this dir and stop
        import_filtered_dir: If set, import docs from this dir and skip Steps 1-3
    """
    try:
        from backend.app.chunk_generator import RecursiveCharacterTextSplitter
    except ImportError:
        from app.chunk_generator import RecursiveCharacterTextSplitter  # type: ignore[no-redef]

    if db_url is None:
        db_url = os.getenv("DATABASE_URL") or _build_db_url_from_env()

    # === IMPORT MODE: Skip Steps 1-3, load from exported data ===
    if import_filtered_dir:
        logger.info("=" * 50)
        logger.info(f"IMPORT MODE: Loading filtered docs from {import_filtered_dir}")
        logger.info("=" * 50)
        docs = import_filtered_docs(import_filtered_dir)
        if not docs:
            logger.error("No documents found in import directory!")
            return 0
        persist_filtered_pdfs(docs, ".data/gst_pdfs")
    else:
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
            all_docs = docs
            docs = batch_ner_filter(docs, nlp)
            del nlp  # Free memory

            persist_filtered_pdfs(docs, ".data/gst_pdfs")
            del all_docs

            if not docs:
                logger.error("No GST-relevant documents found!")
                return 0
        else:
            logger.info("STEP 3: Skipping NER filter")
            for doc in docs:
                doc.is_gst_core = True

            # All docs pass when filter is skipped
            persist_filtered_pdfs(docs, ".data/gst_pdfs")

        # === EXPORT MODE: Save filtered docs and stop ===
        if export_after_filter_dir:
            logger.info("=" * 50)
            logger.info(f"EXPORT MODE: Saving filtered docs to {export_after_filter_dir}")
            logger.info("=" * 50)
            count = export_after_filter(docs, export_after_filter_dir)
            logger.info(f"Exported {count} documents. Pipeline stopping after filter stage.")
            return count

    # === STEP 4: PDF to HTML (parallel) ===
    logger.info("=" * 50)
    logger.info("STEP 4: Converting PDFs to HTML (parallel)")
    logger.info("=" * 50)
    docs = parallel_convert_pdfs(docs)

    # Clean up HC staging directory (PDFs already converted or discarded)
    # Skip in import mode — staging dir belongs to export, not this run
    if not import_filtered_dir:
        staging_dir = ".data/staging/hc_pdfs"
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
            logger.info("Cleaned up HC PDF staging directory")

    # === STEP 5: Chunk and embed (batch) ===
    logger.info("=" * 50)
    logger.info("STEP 5: Chunking and embedding")
    logger.info("=" * 50)

    embed_model: Any
    if use_onnx:
        logger.info(f"Using ONNX Runtime for embeddings (model: {model_name})")
        embed_model = ONNXEmbedder(model_name)
    else:
        from app.embeddings import EmbeddingModel  # type: ignore[no-redef]

        logger.info(f"Loading embedding model: {model_name}")
        embed_model = EmbeddingModel(model_name)

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=4000, chunk_overlap=600, separators=["\n\n", "\n", ".", " ", ""]
    )

    docs = batch_chunk_and_embed(docs, embed_model, text_splitter)
    del embed_model  # Free memory

    # === STEP 6: Database insert (bulk) ===
    logger.info("=" * 50)
    logger.info("STEP 6: Database insert")
    logger.info("=" * 50)
    bulk_insert_to_db(docs, db_url)

    logger.info("=" * 50)
    logger.info(f"PIPELINE COMPLETE: {len(docs)} documents ingested")
    logger.info("=" * 50)

    return len(docs)


def main():
    parser = argparse.ArgumentParser(
        description="Fast unified pipeline for LegalSearch (V3 with Tier 1 optimizations)"
    )
    parser.add_argument("--metadata-dir", default=".data/metadata/raw")
    parser.add_argument("--judgments-dir", default=".data/GST_judgments")
    parser.add_argument("--model-name", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-filter", action="store_true", help="Skip NER filtering")
    parser.add_argument(
        "--use-onnx", action="store_true", help="Use ONNX Runtime for faster embeddings"
    )
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
        help="Skip Steps 1-3, import previously exported docs, run Steps 4-6",
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
        model_name=args.model_name,
        limit=args.limit,
        skip_filter=args.skip_filter,
        use_onnx=args.use_onnx,
        court_type=args.court_type,
        years=args.years,
        export_after_filter_dir=args.export_dir if args.export_after_filter else None,
        import_filtered_dir=args.import_dir if args.import_filtered else None,
    )


if __name__ == "__main__":
    main()
