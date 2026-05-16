"""
Find HC-GST-2024 PDFs referenced in parquet but missing from local tars,
search for them in S3, download, run NER filter, persist GST-relevant ones.

Usage:
    uv run python pipelines/recover_missing_2024.py [--skip-s3-search]

    --skip-s3-search  Skip S3 listing step and use existing
                      .data/logs/missing_2024_found_in_s3.json
"""

import argparse
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
        logging.FileHandler(".data/logs/recover_missing_2024.log"),
    ],
)
log = logging.getLogger(__name__)

BASE_LOCAL = ".data/GST_judgments/HC-GST-2024"
BASE_S3 = "s3://indian-high-court-judgments/data/tar/year=2024"
STAGING_DIR = ".data/staging/recover_missing_2024"
GST_PDFS_DIR = ".data/gst_pdfs"
MISSING_PDFS_PATH = ".data/logs/missing_2024_pdfs.json"
FOUND_IN_S3_PATH = ".data/logs/missing_2024_found_in_s3.json"
NOT_IN_S3_PATH = ".data/logs/missing_2024_not_in_s3.txt"


# ── Step 1: Build local index and find missing PDFs ────────────────────────────

def build_local_index(base: str) -> dict[str, tuple[str, str]]:
    """Return {pdf_filename: (court_dir, bench_dir)} for all locally indexed files."""
    index: dict[str, tuple[str, str]] = {}
    for court in os.listdir(base):
        court_path = os.path.join(base, court)
        if not os.path.isdir(court_path):
            continue
        for bench in os.listdir(court_path):
            idx_path = os.path.join(court_path, bench, "data.index.json")
            if not os.path.exists(idx_path):
                continue
            with open(idx_path) as f:
                d = json.load(f)
            for part in d.get("parts", []):
                for fname in part.get("files", []):
                    index[os.path.basename(fname)] = (court, bench)
    return index


def find_missing_pdfs(local_index: dict, parquet_path: str) -> pd.DataFrame:
    """Return rows from parquet whose PDF filename is not in local_index."""
    df = pd.read_parquet(parquet_path)
    df["pdf_filename"] = df["pdf_link"].apply(lambda x: os.path.basename(str(x)))
    df["bench_raw"] = df["pdf_link"].apply(
        lambda x: str(x).split("cnrorders/")[-1].split("/")[0]
        if "cnrorders/" in str(x) else ""
    )
    df["court_dir"] = df["court_code"].apply(
        lambda x: "court=" + str(x).replace("~", "_")
    )
    missing = df[~df["pdf_filename"].isin(local_index)].drop_duplicates(subset="pdf_filename")
    return missing


# ── Step 2: Search S3 for missing PDFs ────────────────────────────────────────

def list_s3_parts(court: str, bench: str) -> list[str]:
    """List tar part filenames available in S3 for a given court/bench."""
    prefix = f"{BASE_S3}/{court}/bench={bench}/"
    result = subprocess.run(
        ["aws", "s3", "ls", prefix, "--no-sign-request"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning("S3 ls failed for %s: %s", prefix, result.stderr.strip())
        return []
    parts = []
    for line in result.stdout.splitlines():
        parts_col = line.strip().split()
        if parts_col:
            name = parts_col[-1]
            if name.endswith(".tar"):
                parts.append(name)
    return parts


def get_tar_file_list(court: str, bench: str, part: str) -> list[str]:
    """Download only the tar index (first few bytes) — actually we must download
    the full tar to list members. We download to staging and list members."""
    local_dir = os.path.join(BASE_LOCAL, court, f"bench={bench}")
    os.makedirs(local_dir, exist_ok=True)
    local_tar = os.path.join(local_dir, part)

    if not os.path.exists(local_tar):
        s3_url = f"{BASE_S3}/{court}/bench={bench}/{part}"
        log.info("    Downloading %s ...", s3_url)
        r = subprocess.run(
            ["aws", "s3", "cp", s3_url, local_tar, "--no-sign-request"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.warning("    Download failed: %s", r.stderr.strip())
            return []

    try:
        with tarfile.open(local_tar) as tf:
            return [os.path.basename(m.name) for m in tf.getmembers() if m.isfile()]
    except Exception as e:
        log.warning("    Could not list %s: %s", local_tar, e)
        return []


def search_s3_for_missing(missing_df: pd.DataFrame) -> tuple[dict, list]:
    """
    For each bench with missing files, list S3 parts and find which part
    contains each missing file.

    Returns:
        found_in_s3: {pdf_filename: {"court": ..., "bench": ..., "part": ...}}
        not_in_s3:   [pdf_filename, ...]
    """
    # Group missing files by (court_dir, bench_raw)
    groups: dict[tuple, list[str]] = defaultdict(list)
    for _, row in missing_df.iterrows():
        groups[(row["court_dir"], row["bench_raw"])].append(row["pdf_filename"])

    found_in_s3: dict[str, dict] = {}
    not_in_s3: list[str] = []

    for (court_dir, bench_raw), targets in groups.items():
        log.info("Searching S3 for %d files in %s/bench=%s", len(targets), court_dir, bench_raw)
        target_set = set(targets)

        parts = list_s3_parts(court_dir, bench_raw)
        # Exclude data.tar (already have it locally) — search only additional parts
        extra_parts = [p for p in parts if p != "data.tar"]
        log.info("  Extra S3 parts (beyond data.tar): %d", len(extra_parts))

        if not extra_parts:
            log.info("  No extra parts — marking all as not-in-S3")
            not_in_s3.extend(targets)
            continue

        remaining = set(target_set)
        for part in extra_parts:
            if not remaining:
                break
            log.info("  Checking part: %s", part)
            members = get_tar_file_list(court_dir, bench_raw, part)
            for fname in list(remaining):
                if fname in members:
                    found_in_s3[fname] = {
                        "court": court_dir,
                        "bench": f"bench={bench_raw}",
                        "part": part,
                    }
                    remaining.discard(fname)

        not_in_s3.extend(remaining)
        log.info("  Found: %d / %d", len(target_set) - len(remaining), len(target_set))

    return found_in_s3, not_in_s3


# ── Steps 3–7: Download, extract, NER filter, persist ─────────────────────────

def download_and_process(found_in_s3: dict, metadata_by_fname: dict):
    # Group by (court, bench, part)
    parts_to_files: dict[tuple, list[str]] = defaultdict(list)
    for fname, info in found_in_s3.items():
        key = (info["court"], info["bench"], info["part"])
        parts_to_files[key].append(fname)

    already_in_gst = set(os.listdir(GST_PDFS_DIR))
    download_errors = []
    extract_errors = []
    staged_docs = []

    for idx, ((court, bench, part), target_files) in enumerate(parts_to_files.items(), 1):
        log.info("[%d/%d] %s/%s/%s (%d files)", idx, len(parts_to_files),
                 court, bench, part, len(target_files))

        local_dir = os.path.join(BASE_LOCAL, court, bench)
        os.makedirs(local_dir, exist_ok=True)
        local_tar = os.path.join(local_dir, part)

        if not os.path.exists(local_tar):
            s3_url = f"{BASE_S3}/{court}/{bench}/{part}"
            log.info("  Downloading %s ...", s3_url)
            r = subprocess.run(
                ["aws", "s3", "cp", s3_url, local_tar, "--no-sign-request"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                log.error("  Download FAILED: %s", r.stderr.strip())
                download_errors.append({"part": part, "court": court, "bench": bench,
                                        "error": r.stderr.strip()})
                continue
            log.info("  Downloaded OK")
        else:
            log.info("  Already present locally")

        try:
            with tarfile.open(local_tar) as tf:
                member_index = {os.path.basename(m.name): m for m in tf.getmembers()}
                for fname in target_files:
                    member = member_index.get(fname)
                    if member is None:
                        log.warning("  NOT FOUND in tar: %s", fname)
                        extract_errors.append({"fname": fname, "tar": local_tar,
                                               "reason": "not found in tar"})
                        continue
                    out_path = os.path.join(STAGING_DIR, fname)
                    if not os.path.exists(out_path):
                        f = tf.extractfile(member)
                        if f is None:
                            extract_errors.append({"fname": fname, "tar": local_tar,
                                                   "reason": "extractfile returned None"})
                            continue
                        with open(out_path, "wb") as out:
                            out.write(f.read())
                    meta = metadata_by_fname.get(fname, {})
                    staged_docs.append((fname, out_path, meta))
        except Exception as e:
            log.error("  Error reading tar %s: %s", local_tar, e)
            extract_errors.append({"fname": part, "tar": local_tar, "reason": str(e)})

    log.info("Download errors: %d | Extraction errors: %d | Staged: %d",
             len(download_errors), len(extract_errors), len(staged_docs))

    if not staged_docs:
        log.error("No documents staged — aborting NER filter.")
        return download_errors, extract_errors, [], 0

    # NER filter
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pipelines.unified_pipeline import DocumentData, batch_ner_filter, persist_filtered_pdfs, load_ner_model
    from pipelines.shard_hc import parse_title_parties

    fitz.TOOLS.mupdf_display_errors(False)

    docs = []
    text_failures = 0
    for fname, pdf_path, meta in staged_docs:
        cnr = str(meta.get("cnr", fname.replace(".pdf", "")))
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
            year="2024",
            text_content=text,
            pdf_staging_path=pdf_path,
        ))

    log.info("Text failures: %d | Docs for NER: %d", text_failures, len(docs))

    if not docs:
        log.error("No documents to filter.")
        return download_errors, extract_errors, [], text_failures

    nlp = load_ner_model()
    gst_docs = batch_ner_filter(docs, nlp)
    del nlp
    log.info("GST-relevant: %d / %d", len(gst_docs), len(docs))

    persisted = persist_filtered_pdfs(gst_docs, GST_PDFS_DIR)
    return download_errors, extract_errors, gst_docs, text_failures


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-s3-search", action="store_true",
                        help="Use existing missing_2024_found_in_s3.json")
    args = parser.parse_args()

    os.makedirs(STAGING_DIR, exist_ok=True)
    os.makedirs(".data/logs", exist_ok=True)

    # ── 1. Find missing PDFs ───────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("Step 1: Building local index for HC-GST-2024")
    local_index = build_local_index(BASE_LOCAL)
    log.info("Local index size: %d files", len(local_index))

    log.info("Step 1b: Loading parquet metadata")
    parquet_path = ".data/metadata/raw/HC-GST-2024.parquet"
    missing_df = find_missing_pdfs(local_index, parquet_path)
    log.info("Missing PDFs (in parquet, not in local index): %d", len(missing_df))

    # Save missing list
    missing_records = missing_df[["pdf_filename", "cnr", "court_dir", "bench_raw",
                                  "court", "title", "judge", "decision_date"]].to_dict("records")
    with open(MISSING_PDFS_PATH, "w") as f:
        json.dump(missing_records, f, indent=2, default=str)
    log.info("Saved missing list to %s", MISSING_PDFS_PATH)

    # ── 2. Search S3 ───────────────────────────────────────────────────────────
    if args.skip_s3_search:
        log.info("Step 2: Loading existing S3 search results from %s", FOUND_IN_S3_PATH)
        found_in_s3 = json.load(open(FOUND_IN_S3_PATH))
    else:
        log.info("Step 2: Searching S3 year=2024 for missing files")
        found_in_s3, not_in_s3 = search_s3_for_missing(missing_df)

        with open(FOUND_IN_S3_PATH, "w") as f:
            json.dump(found_in_s3, f, indent=2)
        with open(NOT_IN_S3_PATH, "w") as f:
            f.write("\n".join(not_in_s3))
        log.info("Found in S3: %d | Not in S3: %d", len(found_in_s3), len(not_in_s3))
        log.info("Saved to %s and %s", FOUND_IN_S3_PATH, NOT_IN_S3_PATH)

    if not found_in_s3:
        log.warning("No missing files found in S3. Nothing to download.")
        return

    # ── 3. Load parquet metadata keyed by filename ────────────────────────────
    log.info("Step 3: Loading parquet metadata keyed by pdf_filename")
    df = pd.read_parquet(parquet_path)
    df["pdf_filename"] = df["pdf_link"].apply(lambda x: os.path.basename(str(x)))
    df_deduped = df.drop_duplicates(subset="pdf_filename", keep="first")
    metadata_by_fname = df_deduped.set_index("pdf_filename").to_dict("index")
    log.info("Parquet rows loaded: %d (deduped: %d)", len(df), len(df_deduped))

    # ── 4–7. Download, extract, NER, persist ──────────────────────────────────
    log.info("Step 4+: Download / extract / NER filter / persist")
    download_errors, extract_errors, gst_docs, text_failures = download_and_process(
        found_in_s3, metadata_by_fname
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info("Missing from local index:      %d", len(missing_df))
    log.info("Found in S3:                   %d", len(found_in_s3))
    log.info("Not found in S3:               %s",
             len(open(NOT_IN_S3_PATH).read().splitlines()) if os.path.exists(NOT_IN_S3_PATH) else "N/A")
    log.info("Download errors:               %d", len(download_errors))
    log.info("Extraction errors:             %d", len(extract_errors))
    log.info("Text extraction failures:      %d", text_failures)
    log.info("Passed NER filter (GST):       %d", len(gst_docs))
    log.info("=" * 60)

    summary = {
        "missing_from_local": len(missing_df),
        "found_in_s3": len(found_in_s3),
        "download_errors": len(download_errors),
        "extraction_errors": len(extract_errors),
        "text_failures": text_failures,
        "passed_ner_gst": len(gst_docs),
        "gst_cases": [
            {"cnr": d.case_id, "title": d.title, "court": d.court,
             "decision_date": d.decision_date}
            for d in gst_docs
        ],
    }
    summary_path = ".data/logs/recover_missing_2024_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.info("Full summary saved to %s", summary_path)


if __name__ == "__main__":
    main()
