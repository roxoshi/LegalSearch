import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Setup path to allow imports from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.database import Document, init_db
from etl.schemas import DocumentJSON
from etl.transform import Transformer

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("etl_ingest")


def main():
    parser = argparse.ArgumentParser(description="Legal Search ETL Ingestor")
    parser.add_argument(
        "--json-dir", required=True, help="Directory containing JSON metadata files"
    )
    parser.add_argument("--pdf-dir", required=True, help="Directory containing Source PDF files")
    parser.add_argument(
        "--limit", type=int, default=0, help="Limit number of documents to process (0 for all)"
    )

    args = parser.parse_args()

    json_dir = Path(args.json_dir)
    pdf_dir = Path(args.pdf_dir)

    if not json_dir.exists():
        logger.error(f"JSON Directory not found: {json_dir}")
        sys.exit(1)

    # Initialize DB
    logger.info("Connecting to Database...")
    SessionLocal = init_db()

    # Initialize Transformer (loads model, heavy operation)
    model_name = os.getenv("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    logger.info(f"Initializing Transformer with model: {model_name}...")
    transformer = Transformer(model_name=model_name)

    # Scan files
    json_files = sorted(json_dir.rglob("*.json"))
    logger.info(f"Found {len(json_files)} JSON files.")

    if args.limit > 0:
        json_files = json_files[: args.limit]
        logger.info(f"Limiting to {args.limit} files.")

    success_count = 0
    with SessionLocal() as db:
        for json_file in json_files:
            try:
                # 1. READ & VALIDATE
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                # Check 1: Pydantic Validation
                try:
                    doc_json = DocumentJSON(**data)
                except Exception as e:
                    logger.error(f"Validation failed for {json_file.name}: {e}")
                    continue

                # Check 2: Locate PDF
                # Assumption: PDF name matches JSON name (or case_id if configured differently)
                # Plan says: "pair ingestion_data/{id}.json with gst_pdfs/{id}.pdf"
                # Check by filename stem
                pdf_path = pdf_dir / f"{json_file.stem}.pdf"

                # 2. TRANSFORM
                logger.info(f"Processing {doc_json.case_id} ({json_file.name})")
                db_doc, db_chunks = transformer.process_document(doc_json, pdf_path)

                # 3. LOAD (Upsert Logic)
                # Check if exists by case_id to avoid dupes?
                # DB model has unique=True on case_id.
                # Use simple check-and-delete or merge?
                # Simplest for now: Check exist, if so skip or update.
                # Let's skip if exists for efficiency, or delete-reinsert for full update.
                # User wants "robust". Update is better.

                existing = db.query(Document).filter_by(case_id=doc_json.case_id).first()
                if existing:
                    logger.info(f"Updating existing document: {doc_json.case_id}")
                    # Remove old chunks
                    for chunk in existing.chunks:
                        db.delete(chunk)
                    db.delete(existing)
                    db.commit()  # Commit delete first

                # Add new
                db.add(db_doc)
                db.flush()  # Get ID

                for chunk in db_chunks:
                    chunk.document_id = db_doc.id
                    db.add(chunk)

                db.commit()
                success_count += 1
                logger.info(
                    f"Successfully ingested {doc_json.case_id} with {len(db_chunks)} chunks."
                )

            except Exception as e:
                logger.error(f"Error processing {json_file.name}: {e}", exc_info=True)
                db.rollback()
                continue

    logger.info(f"Ingestion Complete. Success: {success_count}/{len(json_files)}")


if __name__ == "__main__":
    main()
