import argparse
import io
import json
import logging
import os
import shutil
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import pdfplumber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("shard_judgments")

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


class FullTextExtractor:
    @staticmethod
    def _find_target_in_zip(zip_file, record_path):
        """Find the matching file inside a zip, trying exact name then _EN suffix."""
        all_files = zip_file.namelist()
        filename = os.path.basename(record_path)

        actual_target = next((f for f in all_files if f.lower().endswith(filename.lower())), None)

        if not actual_target:
            base = filename.rsplit(".", 1)[0]
            en_filename = f"{base}_EN.pdf"
            actual_target = next(
                (f for f in all_files if f.lower().endswith(en_filename.lower())), None
            )

        return actual_target

    @staticmethod
    def extract_text_and_pdf(zip_file, record_path, pdf_output_path):
        """Extract text content and PDF file from an already-open zip in a single pass.

        Returns the extracted text content (empty string on failure).
        """
        try:
            actual_target = FullTextExtractor._find_target_in_zip(zip_file, record_path)
            if not actual_target:
                return ""

            pdf_bytes = zip_file.read(actual_target)

            # Write PDF file
            with open(pdf_output_path, "wb") as dst:
                dst.write(pdf_bytes)

            # Extract text
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                full_content = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        full_content.append(text)
                        if len(full_content) > 6000:
                            full_content = full_content[:6000]
                            break
                return "\n\n".join(full_content).strip()
        except Exception:
            return ""

    @staticmethod
    def extract_from_zip(zip_path, record_path):
        if not os.path.exists(zip_path):
            return ""
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                actual_target = FullTextExtractor._find_target_in_zip(z, record_path)
                if not actual_target:
                    return ""

                with z.open(actual_target) as f:
                    with pdfplumber.open(io.BytesIO(f.read())) as pdf:
                        full_content = []
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                full_content.append(text)
                                if len(full_content) > 6000:
                                    full_content = full_content[:6000]
                                    break
                        return "\n\n".join(full_content).strip()
        except Exception:
            return ""

    @staticmethod
    def extract_pdf_from_zip(zip_path, record_path, output_pdf_path):
        """Extract the actual PDF file from a zip archive to a destination path."""
        if not os.path.exists(zip_path):
            return False
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                actual_target = FullTextExtractor._find_target_in_zip(z, record_path)
                if not actual_target:
                    return False

                with z.open(actual_target) as src:
                    with open(output_pdf_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                return True
        except Exception:
            return False


def _process_chunk(chunk_records, judgments_dir, processed_dir, pdf_output_dir):
    """Process a chunk of records, keeping zips open while year is unchanged.

    Each chunk's records are sorted by year so we minimise zip open/close.
    Returns count of successfully processed records.
    """
    success_count = 0
    current_zip = None
    current_year = None

    try:
        for record in chunk_records:
            cite = record.get("citation")
            year = str(record.get("year"))

            if not cite:
                continue

            # Open new zip when year changes
            if year != current_year:
                if current_zip:
                    current_zip.close()
                    current_zip = None
                zip_name = f"SC-GST-{year}.zip"
                zip_path = os.path.join(judgments_dir, zip_name)
                if os.path.exists(zip_path):
                    current_zip = zipfile.ZipFile(zip_path, "r")
                current_year = year

            if current_zip is None:
                continue

            # Prepare output paths
            year_dir = Path(processed_dir) / f"SC_GST_{year}"
            year_dir.mkdir(parents=True, exist_ok=True)

            pdf_filename = f"{Path(record['path']).stem}.pdf"
            pdf_output_path = Path(pdf_output_dir) / pdf_filename

            # Single-pass: extract text and PDF together
            record["text_content"] = FullTextExtractor.extract_text_and_pdf(
                current_zip, record["path"], pdf_output_path
            )

            # Save individual JSON
            json_filename = f"{Path(record['path']).stem}.json"
            output_path = year_dir / json_filename
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=4, ensure_ascii=False)

            success_count += 1
    finally:
        if current_zip:
            current_zip.close()

    return success_count


def process_and_shard_data(metadata_raw_dir, judgments_dir, processed_dir, pdf_output_dir, workers):
    files = [
        f
        for f in os.listdir(metadata_raw_dir)
        if f.startswith("SC-GST-") and f.endswith(".parquet")
    ]
    logger.info("Loading metadata records...")
    temp_records = []
    for file in files:
        df = pd.read_parquet(os.path.join(metadata_raw_dir, file), columns=KEEP_COLUMNS)
        temp_records.extend(df.to_dict(orient="records"))

    logger.info(f"Loaded {len(temp_records)} total records")

    # Deduplicate by citation, keeping first occurrence
    seen_citations = set()
    unique_records = []
    for record in temp_records:
        cite = record.get("citation")
        if not cite or cite in seen_citations:
            continue
        seen_citations.add(cite)
        unique_records.append(record)

    logger.info(
        f"Processing {len(unique_records)} unique citations (deduped from {len(temp_records)})"
    )

    # Ensure output directories exist
    Path(pdf_output_dir).mkdir(parents=True, exist_ok=True)
    Path(processed_dir).mkdir(parents=True, exist_ok=True)

    # Sort by year for zip locality
    unique_records.sort(key=lambda r: str(r.get("year", "")))

    if workers <= 1:
        # Single-process path (no multiprocessing overhead)
        success_count = _process_chunk(unique_records, judgments_dir, processed_dir, pdf_output_dir)
    else:
        # Split into N roughly equal chunks, each pre-sorted by year
        chunk_size = (len(unique_records) + workers - 1) // workers
        chunks = [
            unique_records[i : i + chunk_size] for i in range(0, len(unique_records), chunk_size)
        ]

        logger.info(f"Distributing {len(unique_records)} records across {len(chunks)} workers")

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_process_chunk, chunk, judgments_dir, processed_dir, pdf_output_dir)
                for chunk in chunks
            ]
            success_count = sum(f.result() for f in futures)

    logger.info(f"DONE: Saved {success_count} JSON files in {processed_dir}")
    logger.info(f"DONE: Extracted PDFs to {pdf_output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Shard GST judgments into individual JSON + PDF files"
    )
    parser.add_argument(
        "--metadata-dir",
        default=".data/metadata/raw",
        help="Directory containing raw parquet metadata files",
    )
    parser.add_argument(
        "--judgments-dir",
        default=".data/GST_judgments",
        help="Directory containing zip archives of PDF judgments",
    )
    parser.add_argument(
        "--output-dir",
        default=".data/metadata/processed",
        help="Directory to write individual JSON files",
    )
    parser.add_argument(
        "--pdf-output-dir",
        default=".data/gst_pdfs",
        help="Directory to extract individual PDF files",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(9, os.cpu_count() or 1),
        help="Number of parallel workers (default: min(9, cpu_count))",
    )
    args = parser.parse_args()

    process_and_shard_data(
        metadata_raw_dir=args.metadata_dir,
        judgments_dir=args.judgments_dir,
        processed_dir=args.output_dir,
        pdf_output_dir=args.pdf_output_dir,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
