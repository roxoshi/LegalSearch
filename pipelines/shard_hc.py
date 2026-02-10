"""
High Court (HC) data extraction module for LegalSearch.

Handles:
- Loading HC parquet metadata (different schema from SC)
- Normalizing HC records to the common DocumentData schema
- Extracting PDFs from hierarchical tar archives (court/bench structure)
"""

from __future__ import annotations

import logging
import os
import re
import tarfile
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed as _as_completed
from multiprocessing import cpu_count
from typing import TYPE_CHECKING

import pandas as pd
from tqdm import tqdm

from pipelines.optimizations import extract_text_pymupdf

if TYPE_CHECKING:
    from pipelines.unified_pipeline import DocumentData

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("shard_hc")

NUM_WORKERS = max(4, cpu_count())

# Regex for splitting title into petitioner/respondent
# Handles: "Vs", "vs", "VS", "v/s", "v."
_VS_PATTERN = re.compile(r"\s+(?:Vs|vs|VS|v/s|v\.)\s+")

# HC parquet columns we need
HC_COLUMNS = [
    "court_code",
    "title",
    "judge",
    "pdf_link",
    "cnr",
    "decision_date",
    "disposal_nature",
    "court",
]


def parse_title_parties(title: str | None) -> tuple[str, str]:
    """Split HC title into (petitioner, respondent).

    HC titles have the format: "CaseType/Number/Year of Petitioner Vs Respondent"
    We extract the parties part after " of " and split on "Vs" variants.

    Returns ("Unknown", "Unknown") if parsing fails.
    """
    if not title:
        return ("Unknown", "Unknown")

    # Try to extract the parties part after " of "
    parties_part = title
    of_idx = title.find(" of ")
    if of_idx != -1:
        parties_part = title[of_idx + 4 :]

    # Split on "Vs" variants
    parts = _VS_PATTERN.split(parties_part, maxsplit=1)
    if len(parts) == 2:
        pet = parts[0].strip()
        resp = parts[1].strip()
        if pet and resp:
            return (pet, resp)

    return ("Unknown", "Unknown")


def normalize_hc_record(record: dict) -> dict:
    """Convert an HC parquet record to the common schema.

    Mappings:
        cnr -> case_id, citation
        title -> title, petitioner, respondent (parsed)
        decision_date -> year (extracted)
        pdf_link -> path (basename)
        court_code, court, judge, decision_date, disposal_nature -> direct
    """
    title = str(record.get("title", ""))
    petitioner, respondent = parse_title_parties(title)

    # Extract year from decision_date (can be Timestamp or string)
    decision_date = record.get("decision_date", "")
    year = ""
    if hasattr(decision_date, "year"):
        # pandas Timestamp
        year = str(decision_date.year)
        decision_date = decision_date.strftime("%Y-%m-%d")
    elif isinstance(decision_date, str) and decision_date:
        # Try YYYY-MM-DD format
        match = re.search(r"(\d{4})", str(decision_date))
        if match:
            year = match.group(1)

    # Extract filename from pdf_link
    pdf_link = str(record.get("pdf_link", ""))
    path = os.path.basename(pdf_link) if pdf_link else ""

    cnr = str(record.get("cnr", ""))

    return {
        "case_id": cnr,
        "citation": cnr,
        "title": title,
        "petitioner": petitioner,
        "respondent": respondent,
        "judge": str(record.get("judge", "Unknown")),
        "court": str(record.get("court", "Unknown Court")),
        "decision_date": str(decision_date),
        "disposal_nature": str(record.get("disposal_nature", "")),
        "year": year,
        "path": path,
        # Preserve HC-specific fields for tar resolution
        "court_code": str(record.get("court_code", "")),
        "pdf_link": pdf_link,
        "_source": "hc",
    }


def load_hc_metadata(metadata_dir: str, years: list[int] | None = None) -> list[dict]:
    """Load HC parquet files and normalize records.

    Args:
        metadata_dir: Directory containing HC-GST-*.parquet files
        years: Optional list of years to filter (e.g. [2018, 2019])

    Returns:
        List of normalized record dicts, deduplicated by cnr.
    """
    files = sorted(
        f for f in os.listdir(metadata_dir) if f.startswith("HC-GST-") and f.endswith(".parquet")
    )

    if years:
        year_strs = {str(y) for y in years}
        files = [f for f in files if any(ys in f for ys in year_strs)]

    if not files:
        logger.warning(f"No HC parquet files found in {metadata_dir}")
        return []

    logger.info(f"Loading HC metadata from {len(files)} parquet files: {files}")

    all_records: list[dict] = []
    for file in files:
        filepath = os.path.join(metadata_dir, file)
        try:
            df = pd.read_parquet(filepath, columns=HC_COLUMNS)
            records = df.to_dict(orient="records")
            all_records.extend(records)
            logger.info(f"  {file}: {len(records)} records")
        except Exception as e:
            logger.error(f"  Failed to load {file}: {e}")

    # Normalize all records
    normalized = [normalize_hc_record(r) for r in all_records]

    # Deduplicate by cnr (case_id)
    seen: set[str] = set()
    unique: list[dict] = []
    for record in normalized:
        cnr = record["case_id"]
        if cnr and cnr not in seen:
            seen.add(cnr)
            unique.append(record)

    logger.info(f"Loaded {len(unique)} unique HC records (from {len(all_records)} total)")
    return unique


def _resolve_tar_path(judgments_dir: str, record: dict) -> tuple[str, str, str]:
    """Compute the tar path, bench, and PDF filename for an HC record.

    HC tar structure: HC-GST-{year}/court={court_code}/bench={bench}/data.tar
    HC pdf_link format: court/cnrorders/{bench}/orders/{filename}.pdf

    Args:
        judgments_dir: Base directory for judgment archives
        record: Normalized HC record dict

    Returns:
        (tar_path, bench, pdf_filename) tuple.
        tar_path is the full path to the tar file.
        pdf_filename is the name to look for inside the tar.
    """
    year = record.get("year", "")
    court_code = record.get("court_code", "")
    pdf_link = record.get("pdf_link", "")

    # Extract bench from pdf_link: court/cnrorders/{bench}/orders/{filename}.pdf
    bench = ""
    parts = pdf_link.replace("\\", "/").split("/")
    if len(parts) >= 4 and parts[1] == "cnrorders":
        bench = parts[2]

    # Extract filename
    pdf_filename = os.path.basename(pdf_link) if pdf_link else ""

    # Build tar path: court_code uses ~ in parquet but _ in directory names
    court_dir = court_code.replace("~", "_")
    tar_path = os.path.join(
        judgments_dir,
        f"HC-GST-{year}",
        f"court={court_dir}",
        f"bench={bench}",
        "data.tar",
    )

    return (tar_path, bench, pdf_filename)


def _extract_single_hc_record(args: tuple[dict, str]) -> DocumentData | None:
    """Extract a single HC record from its tar archive.

    NOTE: This opens the tar for each record — use _extract_tar_batch for
    bulk extraction instead. Kept for backward compatibility with tests.

    Args:
        args: Tuple of (record_dict, judgments_dir)

    Returns:
        DocumentData or None if extraction fails.
    """
    from pipelines.unified_pipeline import DocumentData

    record, judgments_dir = args

    tar_path, _bench, pdf_filename = _resolve_tar_path(judgments_dir, record)

    if not pdf_filename or not os.path.exists(tar_path):
        return None

    try:
        with tarfile.open(tar_path, "r") as tf:
            # Tar files have ./ prefix on entries
            candidates = [f"./{pdf_filename}", pdf_filename]
            member = None
            for candidate in candidates:
                try:
                    member = tf.getmember(candidate)
                    break
                except KeyError:
                    continue

            if member is None:
                return None

            f = tf.extractfile(member)
            if f is None:
                return None

            pdf_bytes = f.read()
            text_content = extract_text_pymupdf(pdf_bytes)

            return DocumentData(
                case_id=record.get("case_id", ""),
                title=record.get("title", ""),
                petitioner=record.get("petitioner", "Unknown"),
                respondent=record.get("respondent", "Unknown"),
                judge=record.get("judge", "Unknown"),
                citation=record.get("citation", ""),
                court=record.get("court", "Unknown Court"),
                decision_date=record.get("decision_date", ""),
                year=record.get("year", ""),
                path=record.get("path", ""),
                text_content=text_content,
                pdf_bytes=pdf_bytes,
            )
    except Exception:
        return None


def _extract_tar_batch(
    args: tuple[str, list[dict], str],
) -> tuple[list[DocumentData | None], list[dict]]:
    """Extract all matching PDFs from a single tar archive in one pass.

    Opens the tar once, builds a member name index, then extracts all
    matching records. PDFs are written to a staging directory on disk
    to avoid holding all pdf_bytes in memory simultaneously.

    Captures per-PDF MuPDF warnings and tracks empty text extractions
    so the caller can write them to an error log.

    Args:
        args: Tuple of (tar_path, records_for_this_tar, staging_dir)

    Returns:
        Tuple of (results, errors).
        results: list of DocumentData or None per record.
        errors: list of dicts with keys: pdf, tar, case_id, warnings, empty_text.
    """
    import fitz  # PyMuPDF

    from pipelines.unified_pipeline import DocumentData

    tar_path, records, staging_dir = args
    errors: list[dict] = []

    fitz.TOOLS.mupdf_display_errors(False)

    if not os.path.exists(tar_path):
        for r in records:
            errors.append({
                "pdf": os.path.basename(r.get("pdf_link", "")),
                "tar": tar_path,
                "case_id": r.get("case_id", ""),
                "warnings": "tar file not found",
                "empty_text": True,
            })
        return [None] * len(records), errors

    results: list[DocumentData | None] = []

    try:
        with tarfile.open(tar_path, "r") as tf:
            # Build a member name -> TarInfo index once for this tar
            member_index: dict[str, tarfile.TarInfo] = {}
            for m in tf.getmembers():
                member_index[m.name] = m
                # Also index without ./ prefix for easy lookup
                stripped = m.name.lstrip("./")
                if stripped != m.name:
                    member_index[stripped] = m

            for record in records:
                pdf_filename = os.path.basename(record.get("pdf_link", ""))
                if not pdf_filename:
                    results.append(None)
                    errors.append({
                        "pdf": "",
                        "tar": tar_path,
                        "case_id": record.get("case_id", ""),
                        "warnings": "empty pdf_link",
                        "empty_text": True,
                    })
                    continue

                # Look up by both prefixed and bare name
                member = member_index.get(f"./{pdf_filename}") or member_index.get(pdf_filename)

                if member is None:
                    results.append(None)
                    errors.append({
                        "pdf": pdf_filename,
                        "tar": tar_path,
                        "case_id": record.get("case_id", ""),
                        "warnings": "file not found in tar",
                        "empty_text": True,
                    })
                    continue

                try:
                    f = tf.extractfile(member)
                    if f is None:
                        results.append(None)
                        continue

                    pdf_bytes = f.read()

                    # Clear MuPDF warning buffer before this PDF
                    fitz.TOOLS.mupdf_warnings()

                    text_content = extract_text_pymupdf(pdf_bytes)

                    # Capture any MuPDF warnings for this specific PDF
                    pdf_warnings = fitz.TOOLS.mupdf_warnings()

                    if pdf_warnings or not text_content:
                        errors.append({
                            "pdf": pdf_filename,
                            "tar": tar_path,
                            "case_id": record.get("case_id", ""),
                            "warnings": pdf_warnings or "no text extracted",
                            "empty_text": not text_content,
                        })

                    # Write PDF to staging dir and free bytes from memory.
                    # Only Step 4 (HTML conversion) needs the PDF again,
                    # and by then the NER filter has discarded ~80%.
                    case_id = record.get("case_id", "")
                    staging_path = os.path.join(staging_dir, f"{case_id}.pdf")
                    with open(staging_path, "wb") as out:
                        out.write(pdf_bytes)
                    del pdf_bytes

                    results.append(
                        DocumentData(
                            case_id=case_id,
                            title=record.get("title", ""),
                            petitioner=record.get("petitioner", "Unknown"),
                            respondent=record.get("respondent", "Unknown"),
                            judge=record.get("judge", "Unknown"),
                            citation=record.get("citation", ""),
                            court=record.get("court", "Unknown Court"),
                            decision_date=record.get("decision_date", ""),
                            year=record.get("year", ""),
                            path=record.get("path", ""),
                            text_content=text_content,
                            pdf_staging_path=staging_path,
                        )
                    )
                except Exception as exc:
                    results.append(None)
                    errors.append({
                        "pdf": pdf_filename,
                        "tar": tar_path,
                        "case_id": record.get("case_id", ""),
                        "warnings": str(exc),
                        "empty_text": True,
                    })
    except Exception as exc:
        for r in records:
            errors.append({
                "pdf": os.path.basename(r.get("pdf_link", "")),
                "tar": tar_path,
                "case_id": r.get("case_id", ""),
                "warnings": f"tar open failed: {exc}",
                "empty_text": True,
            })
        return [None] * len(records), errors

    return results, errors


def _write_error_log(errors: list[dict], log_path: str) -> None:
    """Write extraction errors to a structured log file."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    empty_text_errors = [e for e in errors if e["empty_text"]]
    warning_only_errors = [e for e in errors if not e["empty_text"]]

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("HC PDF Extraction Error Log\n")
        f.write(f"{'=' * 60}\n")
        f.write(f"Total PDFs with issues: {len(errors)}\n")
        f.write(f"  Empty text (unsearchable in DB): {len(empty_text_errors)}\n")
        f.write(f"  Warnings only (text extracted):  {len(warning_only_errors)}\n")
        f.write(f"{'=' * 60}\n\n")

        if empty_text_errors:
            f.write("--- EMPTY TEXT (will be unsearchable) ---\n\n")
            for e in empty_text_errors:
                f.write(f"PDF:     {e['pdf']}\n")
                f.write(f"Case ID: {e['case_id']}\n")
                f.write(f"Tar:     {e['tar']}\n")
                f.write(f"Error:   {e['warnings']}\n\n")

        if warning_only_errors:
            f.write("--- WARNINGS (text partially extracted) ---\n\n")
            for e in warning_only_errors:
                f.write(f"PDF:      {e['pdf']}\n")
                f.write(f"Case ID:  {e['case_id']}\n")
                f.write(f"Tar:      {e['tar']}\n")
                f.write(f"Warnings: {e['warnings']}\n\n")


def parallel_extract_from_tars(
    records: list[dict],
    judgments_dir: str,
    log_dir: str = ".data/logs",
    staging_dir: str = ".data/staging/hc_pdfs",
) -> list[DocumentData]:
    """Extract HC PDFs from tar archives in parallel.

    Groups records by tar path so each tar is opened exactly once per worker.
    Uses ProcessPoolExecutor with tqdm progress tracking.
    Writes PDFs to a staging directory to avoid OOM from holding all
    pdf_bytes in memory. Writes an error log for problem PDFs.

    Args:
        records: List of normalized HC record dicts
        judgments_dir: Base directory for judgment archives
        log_dir: Directory for the extraction error log file
        staging_dir: Directory to stage extracted PDFs on disk

    Returns:
        List of DocumentData objects with text and pdf_staging_path set.
    """
    if not records:
        return []

    # Create staging directory for PDF files
    os.makedirs(staging_dir, exist_ok=True)

    # Group records by tar path — each tar opened once instead of per-record
    tar_groups: dict[str, list[dict]] = {}
    for record in records:
        tar_path, _bench, _pdf = _resolve_tar_path(judgments_dir, record)
        tar_groups.setdefault(tar_path, []).append(record)

    logger.info(
        f"Extracting {len(records)} HC PDFs from {len(tar_groups)} tar archives "
        f"({NUM_WORKERS} workers), staging to {staging_dir}"
    )

    docs: list[DocumentData] = []
    all_errors: list[dict] = []
    batch_args = [(tar_path, recs, staging_dir) for tar_path, recs in tar_groups.items()]

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {
            executor.submit(_extract_tar_batch, args): args[0] for args in batch_args
        }

        with tqdm(total=len(records), desc="HC PDF Extraction", unit="pdf") as pbar:
            for future in _as_completed(futures):
                batch_results, batch_errors = future.result()
                for doc in batch_results:
                    if doc is not None:
                        docs.append(doc)
                all_errors.extend(batch_errors)
                pbar.update(len(batch_results))

    # Write error log if there were any issues
    if all_errors:
        log_path = os.path.join(log_dir, "hc_extraction_errors.log")
        _write_error_log(all_errors, log_path)
        empty_count = sum(1 for e in all_errors if e["empty_text"])
        warn_count = len(all_errors) - empty_count
        logger.warning(
            f"PDF issues: {len(all_errors)} total — "
            f"{empty_count} empty text (unsearchable), "
            f"{warn_count} with MuPDF warnings. "
            f"Details: {log_path}"
        )

    logger.info(f"Extracted {len(docs)}/{len(records)} HC documents")
    return docs
