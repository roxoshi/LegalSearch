import json
import os
import logging
from pathlib import Path
from typing import Iterable, List, Any, Generator
from dotenv import load_dotenv
import numpy as np
from sqlalchemy.orm import Session
from sentence_transformers import SentenceTransformer
from app.database import SessionLocal, engine
from app.models import Document, Base
from sqlalchemy.dialects.postgresql import insert

try:
    load_dotenv()
except Exception as e:
    print("Error in load .env")

MODEL_NAME = os.getenv("MODEL_NAME", None)
BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "100"))
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
def load_documents(file_path: Path) -> List[dict]:
    if not file_path.exists():
        raise FileNotFoundError(f"Data file not found: {file_path}")

    logger.info("Loading documents from %s", file_path)

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)

# Ingestion logic
def chunked(iterable: List[dict], size: int) -> Generator[list[dict], Any, None]:
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]

def ingest_batch(
    db: Session,
    model: SentenceTransformer,
    batch: List[dict],
) -> None:
    contents = [doc["content"] for doc in batch]

    embeddings = model.encode(
        contents,
        show_progress_bar=False,
    ).tolist()

    rows = [
        {
        "title": doc["title"],
        "content": doc["content"],
        "embedding": embeddings[idx]
        }
        for idx, doc in enumerate(batch)
    ]

    db.execute(insert(Document), rows)

def ingest_documents() -> None:
    init_database()
    model = load_model()

    documents = load_documents(DATA_FILE)
    total = len(documents)

    logger.info("Starting ingestion of %d documents", total)

    db: Session = SessionLocal()

    try:
        for idx, batch in enumerate(chunked(documents, BATCH_SIZE), start=1):
            ingest_batch(db, model, batch)
            db.commit()

            processed = min(idx * BATCH_SIZE, total)
            logger.info("Indexed %d / %d documents", processed, total)
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