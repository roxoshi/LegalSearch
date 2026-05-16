import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

# Setup path to allow imports from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.database import Document, init_db

try:
    from backend.app.models import DocumentChunk
except ImportError:
    from app.models import DocumentChunk  # type: ignore[no-redef]
from etl.schemas import AnalysisJSON, MetadataJSON
from etl.transform import Transformer
from pipelines.shard_hc import parse_title_parties

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("etl_ingest")


def load_parquet_metadata(metadata_dir: Path) -> dict[str, MetadataJSON]:
    """Load case metadata from parquet files into a case_id lookup dict.

    Supports two formats:
    - SC format: has a ``case_id`` column
    - HC format: has a ``cnr`` column (used as case_id)

    For HC records, petitioner/respondent are parsed from the title using
    ``parse_title_parties`` (borrowed from pipelines/shard_hc.py).

    Args:
        metadata_dir: Root directory to search for *.parquet files (recursive).

    Returns:
        Mapping of case_id → MetadataJSON.
    """
    lookup: dict[str, MetadataJSON] = {}
    parquet_files = list(metadata_dir.rglob("*.parquet"))

    if not parquet_files:
        logger.warning(f"No parquet files found under {metadata_dir}")
        return lookup

    logger.info(f"Found {len(parquet_files)} parquet files under {metadata_dir}")

    for parquet_file in parquet_files:
        try:
            df = pd.read_parquet(parquet_file)
        except Exception as e:
            logger.warning(f"Could not read {parquet_file}: {e}")
            continue

        columns = set(df.columns)
        is_sc = "case_id" in columns
        is_hc = "cnr" in columns and not is_sc

        if not is_sc and not is_hc:
            logger.debug(f"Skipping {parquet_file}: no case_id or cnr column")
            continue

        for _, row in df.iterrows():
            try:
                if is_sc:
                    case_id = str(row.get("case_id", "")).strip()
                    if not case_id:
                        continue
                    meta = MetadataJSON(
                        case_id=case_id,
                        title=str(row.get("title", "")),
                        petitioner=str(row.get("petitioner", "Unknown")),
                        respondent=str(row.get("respondent", "Unknown")),
                        judge=str(row.get("judge", "Unknown")),
                        citation=str(row.get("citation", "Unknown")),
                        court=str(row.get("court", "Unknown Court")),
                        decision_date=str(row.get("decision_date", "")),
                        disposal_nature=str(row.get("disposal_nature", "")),
                    )
                else:  # HC
                    case_id = str(row.get("cnr", "")).strip()
                    if not case_id:
                        continue
                    title = str(row.get("title", ""))
                    petitioner, respondent = parse_title_parties(title)
                    meta = MetadataJSON(
                        case_id=case_id,
                        title=title,
                        petitioner=petitioner,
                        respondent=respondent,
                        judge=str(row.get("judge", "Unknown")),
                        citation="Not Available",
                        court=str(row.get("court", "Unknown Court")),
                        decision_date=str(row.get("decision_date", "")),
                        disposal_nature=str(row.get("disposal_nature", "")),
                    )

                lookup[case_id] = meta
            except Exception as e:
                logger.debug(f"Skipping row in {parquet_file}: {e}")

    logger.info(f"Loaded {len(lookup)} metadata records")
    return lookup


def main():
    parser = argparse.ArgumentParser(description="Legal Search ETL Ingestor (JSON analysis files)")
    parser.add_argument(
        "--analysis-dir",
        required=True,
        help="Directory containing analysis JSON files (.data/batch_analysis/successes/)",
    )
    parser.add_argument(
        "--metadata-dir",
        default=None,
        help="Directory containing *.parquet metadata files (SC and/or HC format)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of documents to process (0 for all)",
    )

    args = parser.parse_args()

    analysis_dir = Path(args.analysis_dir)
    if not analysis_dir.exists():
        logger.error(f"Analysis directory not found: {analysis_dir}")
        sys.exit(1)

    # Load metadata lookup (optional)
    metadata_lookup: dict[str, MetadataJSON] = {}
    if args.metadata_dir:
        metadata_dir = Path(args.metadata_dir)
        if metadata_dir.exists():
            metadata_lookup = load_parquet_metadata(metadata_dir)
        else:
            logger.warning(f"Metadata directory not found: {metadata_dir}, using defaults")

    # Initialize DB
    logger.info("Connecting to Database...")
    SessionLocal = init_db()

    # Initialize Transformer (loads embedding model — heavy operation)
    model_name = os.getenv("MODEL_NAME", "sentence-transformers/all-mpnet-base-v2")
    logger.info(f"Initializing Transformer with model: {model_name}...")
    transformer = Transformer(model_name=model_name)

    # Discover analysis files
    json_files = sorted(analysis_dir.rglob("*.json"))
    logger.info(f"Found {len(json_files)} JSON files.")

    if args.limit > 0:
        json_files = json_files[: args.limit]
        logger.info(f"Limiting to {args.limit} files.")

    success_count = 0
    failure_count = 0

    with SessionLocal() as db:
        for json_file in json_files:
            case_id = json_file.stem
            try:
                # 1. Parse and validate analysis JSON
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                try:
                    analysis = AnalysisJSON(**data)
                except Exception as e:
                    logger.error(f"Validation failed for {json_file.name}: {e}")
                    failure_count += 1
                    continue

                # 2. Look up metadata (fall back to case_id as title)
                metadata = metadata_lookup.get(
                    case_id, MetadataJSON(case_id=case_id, title=case_id)
                )

                # 3. Transform → Document + chunks
                logger.info(f"Processing {case_id}")
                db_doc, db_chunks = transformer.process_document(case_id, analysis, metadata)

                # 4. Skip if already ingested with chunks — re-analysis is expensive
                existing = db.query(Document).filter_by(case_id=case_id).first()
                if existing:
                    has_chunks = db.query(DocumentChunk).filter_by(document_id=existing.id).first()
                    if has_chunks:
                        logger.info(f"Skipping {case_id}: already in DB with chunks")
                        success_count += 1
                        continue
                    db.delete(existing)
                    db.flush()

                db.add(db_doc)
                db.flush()  # Assign db_doc.id

                for chunk in db_chunks:
                    chunk.document_id = db_doc.id
                    db.add(chunk)

                db.commit()
                success_count += 1
                logger.info(f"Ingested {case_id} with {len(db_chunks)} chunks.")

            except Exception as e:
                logger.error(f"Error processing {json_file.name}: {e}", exc_info=True)
                db.rollback()
                failure_count += 1
                continue

    logger.info(
        f"Ingestion complete. "
        f"Success: {success_count}, Failures: {failure_count}, Total: {len(json_files)}"
    )


if __name__ == "__main__":
    main()
