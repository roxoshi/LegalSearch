"""
Scan all Supreme Court years through the GST classifier pipeline and report
new cases not yet in the DB. Does NOT write anything to DB or extract PDFs.

Pipeline:
  SC zips → keyword_prefilter (S1) → GSTClassifier (S2) → dedup check → report

Usage:
    uv run python -m pipelines.gst_classifier.scan_sc_all_years
    uv run python -m pipelines.gst_classifier.scan_sc_all_years --judgments-dir .data/GST_judgments
"""

from __future__ import annotations

import argparse
import io
import logging
import zipfile
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

JUDGMENTS_DIR = Path(".data/GST_judgments")
METADATA_DIR  = Path(".data/metadata/raw")
MODEL_PATH    = Path(".data/classifier/model.joblib")
TEXT_CHARS    = 6_000
YEARS         = list(range(2017, 2026))


def _pdf_bytes_to_text(pdf_bytes: bytes, max_chars: int) -> str | None:
    try:
        import fitz
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        parts: list[str] = []
        total = 0
        for page in doc:
            t = page.get_text()
            parts.append(t)
            total += len(t)
            if total >= max_chars:
                break
        text = "".join(parts)[:max_chars].strip()
        return text if text else None
    except Exception:
        return None


def load_db_case_ids() -> set[str]:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from etl.database import init_db
    from sqlalchemy import text as sa_text

    log.info("Loading existing case_ids from DB…")
    SessionFactory = init_db()
    session = SessionFactory()
    try:
        rows = session.execute(sa_text("SELECT case_id FROM documents")).fetchall()
        ids = {r[0] for r in rows}
        log.info("DB has %d existing case_ids.", len(ids))
        return ids
    finally:
        session.close()


def build_path_to_case_id(metadata_dir: Path, years: list[int]) -> dict[str, str]:
    """Build {path_stem: case_id} from all SC parquet files."""
    mapping: dict[str, str] = {}
    for year in years:
        pq = metadata_dir / f"SC-GST-{year}.parquet"
        if not pq.exists():
            log.warning("Missing parquet: %s", pq)
            continue
        df = pd.read_parquet(pq, columns=["case_id", "path"])
        for _, row in df.iterrows():
            if pd.notna(row["path"]) and pd.notna(row["case_id"]):
                mapping[str(row["path"]).strip()] = str(row["case_id"]).strip()
    log.info("Parquet index: %d SC path→case_id entries", len(mapping))
    return mapping


def scan_all(judgments_dir: Path, metadata_dir: Path, model_path: Path) -> None:
    from pipelines.optimizations import keyword_prefilter
    from pipelines.gst_classifier.classifier import GSTClassifier

    clf    = GSTClassifier(model_path)
    db_ids = load_db_case_ids()
    path_to_case_id = build_path_to_case_id(metadata_dir, YEARS)

    total_all  = 0
    s1_all     = 0
    s2_all     = 0
    already_all = 0
    new_all    = 0
    all_new_cases: list[dict] = []

    for year in YEARS:
        zip_path = judgments_dir / f"SC-GST-{year}.zip"
        if not zip_path.exists():
            log.warning("Missing zip: %s", zip_path)
            continue

        total = s1_pass = s2_pass = already_in = net_new = 0
        new_cases: list[dict] = []

        log.info("── Year %d ─────────────────────────────", year)
        with zipfile.ZipFile(zip_path) as zf:
            pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
            log.info("  %d PDFs in zip", len(pdf_names))

            for pdf_name in pdf_names:
                total += 1
                # path_stem: strip directory prefix and _EN.pdf suffix
                stem = Path(pdf_name).stem  # e.g. "2023_7_322_346_EN"
                if stem.endswith("_EN"):
                    stem = stem[:-3]        # → "2023_7_322_346"

                try:
                    pdf_bytes = zf.read(pdf_name)
                except Exception:
                    continue

                text = _pdf_bytes_to_text(pdf_bytes, TEXT_CHARS)
                if not text or len(text) < 100:
                    continue

                hit, _ = keyword_prefilter(text)
                if not hit:
                    continue
                s1_pass += 1

                if not clf.is_gst(text):
                    continue
                s2_pass += 1

                case_id = path_to_case_id.get(stem, stem)
                if case_id in db_ids:
                    already_in += 1
                    continue

                net_new += 1
                new_cases.append({"case_id": case_id, "file": pdf_name, "path": stem})

        total_all  += total
        s1_all     += s1_pass
        s2_all     += s2_pass
        already_all += already_in
        new_all    += net_new
        all_new_cases.extend(new_cases)

        print(f"  {year}: scanned={total:,}  s1={s1_pass:,}  s2={s2_pass:,}  in_db={already_in:,}  new={net_new:,}")

        # Save per-year list
        out = Path(f".data/classifier/scan_sc_{year}_new_cases.txt")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            for c in new_cases:
                f.write(f"{c['case_id']}\t{c['file']}\n")
        log.info("  Saved %d new cases → %s", net_new, out)

    # ── Final report ──────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  SCAN REPORT — SC ALL YEARS (2017–2025)")
    print("═" * 60)
    print(f"  Total PDFs scanned       : {total_all:>8,}")
    print(f"  After keyword filter (S1): {s1_all:>8,}  ({s1_all/max(total_all,1)*100:.1f}%)")
    print(f"  After classifier     (S2): {s2_all:>8,}  ({s2_all/max(total_all,1)*100:.1f}%)")
    print(f"  Already in DB            : {already_all:>8,}")
    print(f"  ── Net new cases ──────── : {new_all:>8,}")
    print("═" * 60)

    # Save combined list
    out_all = Path(".data/classifier/scan_sc_all_new_cases.txt")
    with out_all.open("w") as f:
        for c in all_new_cases:
            f.write(f"{c['case_id']}\t{c['file']}\n")
    log.info("Combined new case list → %s (%d cases)", out_all, new_all)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan all SC years through GST classifier")
    parser.add_argument("--judgments-dir", type=Path, default=JUDGMENTS_DIR)
    parser.add_argument("--metadata-dir",  type=Path, default=METADATA_DIR)
    parser.add_argument("--model-path",    type=Path, default=MODEL_PATH)
    args = parser.parse_args()
    scan_all(args.judgments_dir, args.metadata_dir, args.model_path)


if __name__ == "__main__":
    main()
