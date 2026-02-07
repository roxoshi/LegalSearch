"""
Unified Optimized Data Pipeline for LegalSearch - V2 (FAST)

Optimizations:
1. Batch NER inference using spaCy's nlp.pipe() (5-10x faster)
2. Parallel PDF extraction with multiprocessing
3. Parallel PDF to HTML conversion (more workers)
4. Larger embedding batch sizes
5. Process all records in one go (no small batches)
"""

import argparse
import io
import logging
import os
import sys
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import cpu_count
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Suppress noisy warnings before importing ML libraries
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore", message=".*position_ids.*")
warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd
import pdfplumber
from tqdm import tqdm

# Setup path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("unified_pipeline")

# Reduce noise from other loggers
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("safetensors").setLevel(logging.WARNING)

# Constants
KEEP_COLUMNS = [
    'title', 'petitioner', 'respondent', 'judge',
    'citation', 'case_id', 'cnr', 'decision_date',
    'disposal_nature', 'court', 'nc_display', 'year', 'path'
]

GST_STATUTES_KEYWORDS = ["goods and services", "goods & services", "gst"]
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
    pdf_bytes: Optional[bytes] = None
    display_html: Optional[str] = None
    is_gst_core: Optional[bool] = None
    extracted_provisions: List[str] = field(default_factory=list)
    extracted_statutes: List[str] = field(default_factory=list)
    chunks: List[str] = field(default_factory=list)
    embeddings: List[List[float]] = field(default_factory=list)


def load_ner_model():
    """Load spaCy NER model."""
    import spacy

    try:
        import torch
        if torch.cuda.is_available():
            try:
                spacy.require_gpu()
                logger.info("GPU enabled for spaCy")
            except:
                pass
    except ImportError:
        pass

    try:
        nlp = spacy.load("en_legal_ner_trf")
        logger.info("Loaded NER model: en_legal_ner_trf")
    except OSError:
        import en_legal_ner_trf
        nlp = en_legal_ner_trf.load()
        logger.info("Loaded NER model via direct import")

    return nlp


def batch_ner_filter(docs: List[DocumentData], nlp) -> List[DocumentData]:
    """
    Filter documents using BATCH NER inference with nlp.pipe().
    This is 5-10x faster than processing one at a time.
    """
    texts = [doc.text_content[:MAX_TEXT_CHARS] for doc in docs]

    logger.info(f"Running batch NER on {len(texts)} documents...")

    # Use nlp.pipe for batch processing - MUCH faster
    # n_process=1 because transformer models don't parallelize well
    # batch_size controls how many docs to process at once in GPU memory
    processed_docs = list(tqdm(
        nlp.pipe(texts, batch_size=32, n_process=1),
        total=len(texts),
        desc="NER Inference",
        unit="doc"
    ))

    relevant = []
    for doc, spacy_doc in zip(docs, processed_docs):
        provisions = set()
        statutes = set()

        for ent in spacy_doc.ents:
            if ent.label_ == "PROVISION":
                provisions.add(ent.text.strip())
            elif ent.label_ == "STATUTE":
                statutes.add(ent.text.strip())

        doc.extracted_provisions = sorted(provisions)
        doc.extracted_statutes = sorted(statutes)

        # Check GST relevance
        statutes_text = " ".join(statutes).lower()
        doc.is_gst_core = any(kw in statutes_text for kw in GST_STATUTES_KEYWORDS)

        if doc.is_gst_core:
            relevant.append(doc)

    logger.info(f"NER Filter: {len(relevant)}/{len(docs)} documents are GST-relevant")
    return relevant


def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Extract text from PDF bytes."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            content = []
            total_len = 0
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    content.append(text)
                    total_len += len(text)
                    if total_len > max_chars:
                        break
            return "\n\n".join(content).strip()[:max_chars]
    except:
        return ""


def _extract_single_record(args) -> Optional[DocumentData]:
    """Extract a single record - for parallel processing."""
    record, judgments_dir = args
    cite = record.get('citation')
    year = str(record.get('year', ''))

    if not cite:
        return None

    zip_path = os.path.join(judgments_dir, f"SC-GST-{year}.zip")
    if not os.path.exists(zip_path):
        return None

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            all_files = zf.namelist()
            filename = os.path.basename(record.get('path', ''))

            # Find target file
            target = next((f for f in all_files if f.lower().endswith(filename.lower())), None)
            if not target:
                base = filename.rsplit('.', 1)[0]
                en_filename = f"{base}_EN.pdf"
                target = next((f for f in all_files if f.lower().endswith(en_filename.lower())), None)

            if not target:
                return None

            pdf_bytes = zf.read(target)
            text_content = extract_text_from_pdf_bytes(pdf_bytes)

            return DocumentData(
                case_id=record.get('case_id', ''),
                title=record.get('title', ''),
                petitioner=record.get('petitioner', 'Unknown'),
                respondent=record.get('respondent', 'Unknown'),
                judge=record.get('judge', 'Unknown'),
                citation=cite,
                court=record.get('court', 'Unknown Court'),
                decision_date=record.get('decision_date', ''),
                year=year,
                path=record.get('path', ''),
                text_content=text_content,
                pdf_bytes=pdf_bytes,
            )
    except:
        return None


def parallel_extract_from_zips(records: List[Dict], judgments_dir: str) -> List[DocumentData]:
    """Extract PDFs in parallel using ProcessPoolExecutor."""
    logger.info(f"Extracting {len(records)} PDFs in parallel ({NUM_WORKERS} workers)...")

    args_list = [(r, judgments_dir) for r in records]

    docs = []
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        results = list(tqdm(
            executor.map(_extract_single_record, args_list, chunksize=50),
            total=len(args_list),
            desc="PDF Extraction",
            unit="pdf"
        ))

    docs = [d for d in results if d is not None]
    logger.info(f"Extracted {len(docs)}/{len(records)} documents")
    return docs


def _convert_single_pdf(doc: DocumentData) -> DocumentData:
    """Convert single PDF to HTML."""
    from pipelines.convert_pdf import convert_pdf_to_html
    import tempfile

    if doc.pdf_bytes:
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
                f.write(doc.pdf_bytes)
                temp_path = f.name

            try:
                doc.display_html = convert_pdf_to_html(Path(temp_path))
            finally:
                os.unlink(temp_path)
        except:
            doc.display_html = f"<p>{doc.text_content}</p>"
    else:
        doc.display_html = f"<p>{doc.text_content}</p>"

    doc.pdf_bytes = None  # Free memory
    return doc


def parallel_convert_pdfs(docs: List[DocumentData]) -> List[DocumentData]:
    """Convert PDFs to HTML in parallel."""
    logger.info(f"Converting {len(docs)} PDFs to HTML ({NUM_WORKERS} workers)...")

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        results = list(tqdm(
            executor.map(_convert_single_pdf, docs),
            total=len(docs),
            desc="PDF to HTML",
            unit="doc"
        ))

    return results


def batch_chunk_and_embed(docs: List[DocumentData], model, splitter) -> List[DocumentData]:
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
    logger.info(f"Generating embeddings for {total_chunks} chunks (batch size: {EMBEDDING_BATCH_SIZE})...")

    all_embeddings = []
    for i in tqdm(range(0, total_chunks, EMBEDDING_BATCH_SIZE), desc="Embeddings", unit="batch"):
        batch = all_chunks[i:i + EMBEDDING_BATCH_SIZE]
        batch_embeddings = model.encode(batch, show_progress_bar=False, batch_size=128)
        all_embeddings.extend(batch_embeddings.tolist())

    # Distribute embeddings back
    idx = 0
    for doc, count in zip(docs, chunk_counts):
        doc.embeddings = all_embeddings[idx:idx + count]
        idx += count

    return docs


def bulk_insert_to_db(docs: List[DocumentData], db_url: str):
    """Insert all documents to database using bulk operations."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    try:
        from backend.app.models import Base, Document, DocumentChunk
    except ImportError:
        from app.models import Base, Document, DocumentChunk

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
            db.execute(text("DELETE FROM document_chunks WHERE document_id IN (SELECT id FROM documents WHERE case_id = ANY(:ids))"), {"ids": list(overlap)})
            db.execute(text("DELETE FROM documents WHERE case_id = ANY(:ids)"), {"ids": list(overlap)})
            db.commit()

        # Bulk insert documents
        logger.info("Inserting documents...")
        db_docs = []
        for doc in tqdm(docs, desc="Preparing docs", unit="doc"):
            db_docs.append(Document(
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
            ))

        db.bulk_save_objects(db_docs, return_defaults=True)
        db.flush()

        # Get IDs
        doc_id_map = {r[0]: r[1] for r in db.execute(text("SELECT case_id, id FROM documents WHERE case_id = ANY(:ids)"), {"ids": case_ids}).fetchall()}

        # Bulk insert chunks
        logger.info("Inserting chunks...")
        db_chunks = []
        for doc in tqdm(docs, desc="Preparing chunks", unit="doc"):
            doc_id = doc_id_map.get(doc.case_id)
            if doc_id:
                for chunk_text, embedding in zip(doc.chunks, doc.embeddings):
                    db_chunks.append(DocumentChunk(
                        document_id=doc_id,
                        chunk_content=chunk_text,
                        embedding=embedding
                    ))

        logger.info(f"Inserting {len(db_chunks)} chunks...")

        # Insert chunks in batches to avoid memory issues
        batch_size = 5000
        for i in tqdm(range(0, len(db_chunks), batch_size), desc="Chunk batches", unit="batch"):
            batch = db_chunks[i:i + batch_size]
            db.bulk_save_objects(batch)
            db.flush()

        db.commit()

    logger.info("Database insert complete")


def load_metadata(metadata_dir: str) -> List[Dict]:
    """Load and deduplicate metadata from parquet files."""
    files = [f for f in os.listdir(metadata_dir) if f.endswith('.parquet')]

    all_records = []
    for file in files:
        df = pd.read_parquet(os.path.join(metadata_dir, file), columns=KEEP_COLUMNS)
        all_records.extend(df.to_dict(orient='records'))

    # Deduplicate by citation
    seen = set()
    unique = []
    for record in all_records:
        cite = record.get('citation')
        if cite and cite not in seen:
            seen.add(cite)
            unique.append(record)

    logger.info(f"Loaded {len(unique)} unique records (from {len(all_records)} total)")
    return unique


def run_pipeline(
    metadata_dir: str,
    judgments_dir: str,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    db_url: str = None,
    limit: int = 0,
    skip_filter: bool = False
):
    """Run the optimized pipeline - processes everything in one go."""
    from sentence_transformers import SentenceTransformer

    try:
        from backend.app.chunk_generator import RecursiveCharacterTextSplitter
    except ImportError:
        from app.chunk_generator import RecursiveCharacterTextSplitter

    if db_url is None:
        db_url = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/search_db")

    # === STEP 1: Load metadata ===
    logger.info("=" * 50)
    logger.info("STEP 1: Loading metadata")
    logger.info("=" * 50)
    records = load_metadata(metadata_dir)

    if limit > 0:
        records = records[:limit]
        logger.info(f"Limited to {limit} records")

    # === STEP 2: Parallel PDF extraction ===
    logger.info("=" * 50)
    logger.info("STEP 2: Extracting PDFs (parallel)")
    logger.info("=" * 50)
    docs = parallel_extract_from_zips(records, judgments_dir)

    if not docs:
        logger.error("No documents extracted!")
        return 0

    # === STEP 3: NER Filter (batch processing) ===
    if not skip_filter:
        logger.info("=" * 50)
        logger.info("STEP 3: NER Filtering (batch)")
        logger.info("=" * 50)
        nlp = load_ner_model()
        docs = batch_ner_filter(docs, nlp)
        del nlp  # Free memory

        if not docs:
            logger.error("No GST-relevant documents found!")
            return 0
    else:
        logger.info("STEP 3: Skipping NER filter")
        for doc in docs:
            doc.is_gst_core = True

    # === STEP 4: PDF to HTML (parallel) ===
    logger.info("=" * 50)
    logger.info("STEP 4: Converting PDFs to HTML (parallel)")
    logger.info("=" * 50)
    docs = parallel_convert_pdfs(docs)

    # === STEP 5: Chunk and embed (batch) ===
    logger.info("=" * 50)
    logger.info("STEP 5: Chunking and embedding")
    logger.info("=" * 50)
    logger.info(f"Loading embedding model: {model_name}")
    embed_model = SentenceTransformer(model_name)

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=4000,
        chunk_overlap=600,
        separators=["\n\n", "\n", ".", " ", ""]
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
    parser = argparse.ArgumentParser(description="Fast unified pipeline for LegalSearch")
    parser.add_argument("--metadata-dir", default=".data/metadata/raw")
    parser.add_argument("--judgments-dir", default=".data/GST_judgments")
    parser.add_argument("--model-name", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-filter", action="store_true")

    args = parser.parse_args()

    run_pipeline(
        metadata_dir=args.metadata_dir,
        judgments_dir=args.judgments_dir,
        model_name=args.model_name,
        limit=args.limit,
        skip_filter=args.skip_filter,
    )


if __name__ == "__main__":
    main()
