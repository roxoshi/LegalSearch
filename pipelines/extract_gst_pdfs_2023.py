"""Extract GST-classified 2023 HC PDFs from tar archives to .data/gst_pdfs/.

Reads scan_2023_new_cases.txt (case_id + pdf_filename), builds a lookup from
all data.index.json files, then extracts matched PDFs from their tars.

Output: .data/gst_pdfs/{case_id}.pdf  (matching existing naming convention)
"""

from __future__ import annotations

import json
import logging
import tarfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
HC_2023_DIR = ROOT / ".data/GST_judgments/HC-GST-2023"
CASE_LIST = ROOT / ".data/classifier/scan_2023_new_cases.txt"
OUTPUT_DIR = ROOT / ".data/gst_pdfs"


def build_pdf_to_tar_index(hc_dir: Path) -> dict[str, Path]:
    """Scan all data.index.json files and return {pdf_filename: tar_path}."""
    index: dict[str, Path] = {}
    for idx_file in sorted(hc_dir.rglob("data.index.json")):
        tar_path = idx_file.parent / "data.tar"
        if not tar_path.exists():
            logger.warning("Missing tar: %s", tar_path)
            continue
        with idx_file.open() as f:
            data = json.load(f)
        for part in data.get("parts", []):
            for filename in part.get("files", []):
                index[filename] = tar_path
    logger.info("Indexed %d PDF filenames across %d tars", len(index), len({v for v in index.values()}))
    return index


def load_case_list(case_list_path: Path) -> list[tuple[str, str]]:
    """Return list of (case_id, pdf_filename) from scan_2023_new_cases.txt."""
    cases = []
    with case_list_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) == 2:
                cases.append((parts[0], parts[1]))
    return cases


def extract_pdfs(
    cases: list[tuple[str, str]],
    pdf_to_tar: dict[str, Path],
    output_dir: Path,
) -> tuple[int, int]:
    """Extract PDFs from tars, saving as {case_id}.pdf. Returns (success, missing)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group by tar to open each tar only once
    tar_to_cases: dict[Path, list[tuple[str, str]]] = {}
    missing = []
    for case_id, pdf_filename in cases:
        tar_path = pdf_to_tar.get(pdf_filename)
        if tar_path is None:
            missing.append((case_id, pdf_filename))
            continue
        tar_to_cases.setdefault(tar_path, []).append((case_id, pdf_filename))

    if missing:
        logger.warning("%d PDFs not found in any index:", len(missing))
        for case_id, fn in missing[:10]:
            logger.warning("  %s -> %s", case_id, fn)
        if len(missing) > 10:
            logger.warning("  ... and %d more", len(missing) - 10)

    succeeded = 0
    skipped = 0
    for tar_path, tar_cases in sorted(tar_to_cases.items()):
        logger.info("Opening %s (%d cases)", tar_path.relative_to(ROOT), len(tar_cases))
        try:
            with tarfile.open(tar_path, "r") as tf:
                members_by_name = {Path(m.name).name: m for m in tf.getmembers() if m.isfile()}
                for case_id, pdf_filename in tar_cases:
                    out_path = output_dir / f"{case_id}.pdf"
                    if out_path.exists():
                        skipped += 1
                        continue
                    member = members_by_name.get(pdf_filename)
                    if member is None:
                        logger.warning("  Not in tar: %s", pdf_filename)
                        missing.append((case_id, pdf_filename))
                        continue
                    f = tf.extractfile(member)
                    if f is None:
                        logger.warning("  Could not extract: %s", pdf_filename)
                        continue
                    out_path.write_bytes(f.read())
                    succeeded += 1
        except Exception as exc:
            logger.error("Failed to open %s: %s", tar_path, exc)

    logger.info(
        "Done — extracted: %d | skipped (already exist): %d | missing: %d",
        succeeded, skipped, len(missing),
    )
    return succeeded, len(missing)


def main() -> None:
    logger.info("Building PDF → tar index from %s", HC_2023_DIR)
    pdf_to_tar = build_pdf_to_tar_index(HC_2023_DIR)

    logger.info("Loading case list from %s", CASE_LIST)
    cases = load_case_list(CASE_LIST)
    logger.info("Cases to extract: %d", len(cases))

    extract_pdfs(cases, pdf_to_tar, OUTPUT_DIR)


if __name__ == "__main__":
    main()
