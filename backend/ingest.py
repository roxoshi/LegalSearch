import glob
import json
import logging
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from app.chunk_generator import RecursiveCharacterTextSplitter
from app.database import SessionLocal, engine
from app.models import Base, Document, DocumentChunk

# Add parent directory to path to import pipelines
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipelines.convert_pdf import convert_pdf_to_html

try:
    load_dotenv()
except Exception:
    print("Error in load .env")

MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")
BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "50"))
DATA_FILE = Path(os.getenv("DOCUMENTS_FILE", "documents.json"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# logging
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("document_ingestion")


# Initialization
def init_database() -> None:
    logger.info("Ensuring database schema exists")
    Base.metadata.create_all(bind=engine)


def load_model() -> SentenceTransformer:
    logger.info("Loading embedding model: %s", MODEL_NAME)
    return SentenceTransformer(MODEL_NAME)


# Data loading
def load_documents(file_path: Path) -> list[dict]:
    if not file_path.exists():
        raise FileNotFoundError(f"Data file not found: {file_path}")
    logger.info("Loading documents from %s", file_path)
    files = glob.glob(f"{file_path}/*.json")
    documents: list[dict] = []
    for file in files:
        with Path(file).open("r", encoding="utf-8") as f:
            documents.append(json.load(f))
    return documents


# Ingestion logic
def chunked(iterable: list[dict], size: int) -> Generator[list[dict], Any, None]:
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def ingest_batch(
    db: Session,
    model: SentenceTransformer,
    batch: list[dict],
) -> None:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=4000, chunk_overlap=600, separators=["\n\n", "\n", ".", " ", ""]
    )
    try:
        for doc_data in batch:
            # Try to find corresponding PDF
            # Assuming file_name is available or can be derived.
            # If JSON has 'source' or 'file_name', use it.
            # Fallback: check if 'case_id' matches filename.

            display_html = None

            # Construct PDF path (adjust based on actual PDF location)
            pdf_name = f"{doc_data['case_id']}.pdf"  # Example assumption
            pdf_path = Path("/app/.data/gst_pdfs") / pdf_name

            if not pdf_path.exists():
                # Try finding by title or other metadata if needed,
                # or maybe the JSON has 'filename' field?
                # For now, trying case_id and title sanitization
                pass

            if pdf_path.exists():
                logger.info(f"Converting PDF for {doc_data['case_id']}")
                display_html = convert_pdf_to_html(pdf_path)

            # If conversion failed or no PDF, fallback to simple text formatting
            if not display_html:
                display_html = f"<p>{doc_data['text_content']}</p>"

            new_doc = Document(
                title=doc_data["title"],
                petitioner=doc_data.get("petitioner", "N/A"),
                respondent=doc_data.get("respondent", "N/A"),
                judge=doc_data.get("judge", "N/A"),
                citation=doc_data.get("citation", "N/A"),
                decision_date=doc_data.get("decision_date", "N/A"),
                court=doc_data.get("court", "N/A"),
                case_id=doc_data["case_id"],
                content=doc_data["text_content"],
                display_content=display_html,
            )
            db.add(new_doc)
            db.flush()
            text_chunks = text_splitter.split_text(doc_data["text_content"])
            chunk_embeddings = model.encode(text_chunks, show_progress_bar=False).tolist()
            chunk_objects = [
                DocumentChunk(document_id=new_doc.id, chunk_content=text, embedding=embedding)
                for text, embedding in zip(text_chunks, chunk_embeddings)
            ]

            db.add_all(chunk_objects)
        db.commit()
        logger.info(f"Successfully commited batch of {len(batch)} documents")

    except Exception as e:
        db.rollback()
        logger.error(f"Error processing batch: {e}")
        raise


def ingest_documents() -> None:
    init_database()
    model = load_model()

    documents = load_documents(DATA_FILE)
    total = len(documents)

    logger.info("Starting ingestion of %d documents", total)

    db: Session = SessionLocal()

    # Check if documents already exist
    existing_count = db.query(Document).count()
    if existing_count > 0:
        logger.info(f"Database already contains {existing_count} documents. Skipping ingestion.")
        db.close()
        return

    try:
        for i in range(0, total, BATCH_SIZE):
            batch = documents[i : i + BATCH_SIZE]
            ingest_batch(db, model, batch)
            logger.info(f"Processed {i + len(batch)} / {total} documents")
        db.commit()
    except Exception:
        logger.exception("Ingestion failed, rolling back transaction")
        db.rollback()
        raise

    finally:
        db.close()
        logger.info("Database session closed")

    logger.info("Ingestion completed successfully")


if __name__ == "__main__":
    ingest_documents()
