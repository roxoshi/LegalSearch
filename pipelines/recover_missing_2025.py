"""
Download missing HC-GST-2025 tar parts, extract target PDFs, run NER filter,
persist GST-relevant ones to .data/gst_pdfs.

Usage:
    uv run python pipelines/recover_missing_2025.py
"""

import json
import logging
import os
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

import fitz
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(".data/logs/recover_missing_2025.log"),
    ],
)
log = logging.getLogger(__name__)

BASE_LOCAL = ".data/GST_judgments/HC-GST-2025"
BASE_S3 = "s3://indian-high-court-judgments/data/tar/year=2025"
STAGING_DIR = ".data/staging/recover_missing_2025"
GST_PDFS_DIR = ".data/gst_pdfs"
FOUND_IN_S3_PATH = ".data/logs/missing_2025_found_in_s3.json"


def main():
    os.makedirs(STAGING_DIR, exist_ok=True)
    os.makedirs(".data/logs", exist_ok=True)

    # ── Step 1: Load target file list ──────────────────────────────────────────
    log.info("Loading target file list from %s", FOUND_IN_S3_PATH)
    found_in_s3: dict[str, dict] = json.load(open(FOUND_IN_S3_PATH))
    log.info("Target files: %d", len(found_in_s3))

    # ── Step 2: Load parquet metadata keyed by PDF filename ───────────────────
    log.info("Loading HC-GST-2025 parquet metadata...")
    df = pd.read_parquet(".data/metadata/raw/HC-GST-2025.parquet")
    df["pdf_filename"] = df["pdf_link"].apply(lambda x: os.path.basename(str(x)))
    df_deduped = df.drop_duplicates(subset="pdf_filename", keep="first")
    log.info("Parquet rows after dedup on pdf_filename: %d (dropped %d dupes)",
             len(df_deduped), len(df) - len(df_deduped))
    metadata_by_fname = df_deduped.set_index("pdf_filename").to_dict("index")
    log.info("Parquet rows loaded: %d", len(df))

    # ── Step 3: Group targets by (court, bench, part) ─────────────────────────
    parts_to_files: dict[tuple, list[str]] = defaultdict(list)
    for fname, info in found_in_s3.items():
        key = (info["court"], info["bench"], info["part"])
        parts_to_files[key].append(fname)
    log.info("Tar parts to process: %d", len(parts_to_files))

    # ── Step 4: Download tars and extract target PDFs ─────────────────────────
    already_in_gst = set(os.listdir(GST_PDFS_DIR))
    download_errors = []
    extract_errors = []
    staged_docs = []  # list of (fname, pdf_path, meta)

    for idx, ((court, bench, part), target_files) in enumerate(parts_to_files.items(), 1):
        log.info("[%d/%d] Processing %s/%s/%s (%d files)",
                 idx, len(parts_to_files), court, bench, part, len(target_files))

        local_dir = os.path.join(BASE_LOCAL, court, bench)
        os.makedirs(local_dir, exist_ok=True)
        local_tar = os.path.join(local_dir, part)

        # Download if not already present
        if not os.path.exists(local_tar):
            s3_url = f"{BASE_S3}/{court}/{bench}/{part}"
            log.info("  Downloading %s...", s3_url)
            result = subprocess.run(
                ["aws", "s3", "cp", s3_url, local_tar, "--no-sign-request"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log.error("  Download FAILED: %s", result.stderr.strip())
                download_errors.append({"part": part, "court": court, "bench": bench,
                                        "error": result.stderr.strip()})
                continue
            log.info("  Downloaded OK")
        else:
            log.info("  Already present locally, skipping download")

        # Extract only target files from this tar
        target_set = set(target_files)
        extracted_count = 0

        try:
            with tarfile.open(local_tar) as tf:
                member_index: dict[str, tarfile.TarInfo] = {}
                for m in tf.getmembers():
                    bare = os.path.basename(m.name)
                    member_index[bare] = m

                for fname in target_files:
                    member = member_index.get(fname)
                    if member is None:
                        log.warning("  NOT FOUND in tar: %s", fname)
                        extract_errors.append({"fname": fname, "tar": local_tar,
                                               "reason": "not found in tar"})
                        continue

                    out_path = os.path.join(STAGING_DIR, fname)
                    if os.path.exists(out_path):
                        # Already staged from a previous run
                        meta = metadata_by_fname.get(fname, {})
                        staged_docs.append((fname, out_path, meta))
                        extracted_count += 1
                        continue

                    f = tf.extractfile(member)
                    if f is None:
                        extract_errors.append({"fname": fname, "tar": local_tar,
                                               "reason": "extractfile returned None"})
                        continue

                    with open(out_path, "wb") as out:
                        out.write(f.read())

                    meta = metadata_by_fname.get(fname, {})
                    staged_docs.append((fname, out_path, meta))
                    extracted_count += 1

        except Exception as e:
            log.error("  Error reading tar %s: %s", local_tar, e)
            extract_errors.append({"fname": part, "tar": local_tar, "reason": str(e)})
            continue

        log.info("  Extracted %d/%d target files", extracted_count, len(target_files))

    log.info("=" * 60)
    log.info("Download errors: %d", len(download_errors))
    log.info("Extraction errors: %d", len(extract_errors))
    log.info("Total staged for NER filter: %d", len(staged_docs))
    log.info("=" * 60)

    if not staged_docs:
        log.error("No documents staged — aborting.")
        sys.exit(1)

    # ── Step 5: Build DocumentData, extract text ───────────────────────────────
    log.info("Extracting text from PDFs...")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pipelines.unified_pipeline import DocumentData, batch_ner_filter, persist_filtered_pdfs
    from pipelines.shard_hc import parse_title_parties

    fitz.TOOLS.mupdf_display_errors(False)

    docs = []
    text_failures = 0

    for fname, pdf_path, meta in staged_docs:
        cnr = str(meta.get("cnr", fname.replace(".pdf", "")))

        # Skip if already in gst_pdfs
        if f"{cnr}.pdf" in already_in_gst:
            log.debug("Already in gst_pdfs, skipping: %s", cnr)
            continue

        try:
            pdf_doc = fitz.open(pdf_path)
            text = " ".join(page.get_text() for page in pdf_doc)
            pdf_doc.close()
        except Exception as e:
            log.warning("Text extraction failed for %s: %s", fname, e)
            text_failures += 1
            continue

        if not text.strip():
            log.debug("Empty text, skipping: %s", fname)
            text_failures += 1
            continue

        title = str(meta.get("title", ""))
        petitioner, respondent = parse_title_parties(title)

        docs.append(DocumentData(
            case_id=cnr,
            title=title,
            petitioner=petitioner,
            respondent=respondent,
            judge=str(meta.get("judge", "Unknown")),
            court=str(meta.get("court", "Unknown Court")),
            decision_date=str(meta.get("decision_date", "")),
            year="2025",
            text_content=text,
            pdf_staging_path=pdf_path,
        ))

    log.info("Text extraction failures: %d", text_failures)
    log.info("Documents ready for NER filter: %d", len(docs))

    if not docs:
        log.error("No documents to filter — aborting.")
        sys.exit(1)

    # ── Step 6: NER filter ─────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("Running NER filter...")
    log.info("=" * 60)
    from pipelines.unified_pipeline import load_ner_model
    nlp = load_ner_model()
    gst_docs = batch_ner_filter(docs, nlp)
    del nlp
    log.info("GST-relevant documents: %d / %d", len(gst_docs), len(docs))

    # ── Step 7: Persist ────────────────────────────────────────────────────────
    log.info("Persisting %d GST PDFs to %s...", len(gst_docs), GST_PDFS_DIR)
    persisted = persist_filtered_pdfs(gst_docs, GST_PDFS_DIR)

    # ── Step 8: Summary ────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info("Target files from S3 index:    %d", len(found_in_s3))
    log.info("Download errors:               %d", len(download_errors))
    log.info("Extraction errors:             %d", len(extract_errors))
    log.info("Text extraction failures:      %d", text_failures)
    log.info("Sent to NER filter:            %d", len(docs))
    log.info("Passed NER filter (GST):       %d", len(gst_docs))
    log.info("Persisted to gst_pdfs:         %d", persisted)
    log.info("=" * 60)

    # Save summary JSON
    summary = {
        "target_files": len(found_in_s3),
        "download_errors": len(download_errors),
        "extraction_errors": len(extract_errors),
        "text_failures": text_failures,
        "sent_to_ner": len(docs),
        "passed_ner_gst": len(gst_docs),
        "persisted": persisted,
        "gst_cases": [
            {"cnr": d.case_id, "title": d.title, "court": d.court,
             "decision_date": d.decision_date}
            for d in gst_docs
        ],
    }
    summary_path = ".data/logs/recover_missing_2025_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.info("Full summary saved to %s", summary_path)


if __name__ == "__main__":
    main()
