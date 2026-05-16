"""
Scan one year of HC judgments through the GST filter pipeline and report
how many cases are new vs already in DB. Does NOT write anything to DB.

Pipeline:
  HC tars → keyword_prefilter (stage 1) → GSTClassifier (stage 2) → dedup check → report

Usage:
    uv run python -m pipelines.gst_classifier.scan_year --year 2023
    uv run python -m pipelines.gst_classifier.scan_year --year 2023 --judgments-dir .data/GST_judgments
"""

from __future__ import annotations

import argparse
import io
import logging
import tarfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

JUDGMENTS_DIR = Path(".data/GST_judgments")
MODEL_PATH    = Path(".data/classifier/model.joblib")
TEXT_CHARS    = 6_000


def _cnr_from_member(name: str) -> str:
    """Extract CNR from tar member name like ./HPHC010103602020_1_2023-11-06.pdf"""
    basename = name.lstrip("./")
    return basename.split("_")[0]


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
    """Load all existing case_ids from DB for dedup."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from etl.database import init_db
    from sqlalchemy import text

    log.info("Loading existing case_ids from DB…")
    SessionFactory = init_db()
    session = SessionFactory()
    try:
        rows = session.execute(text("SELECT case_id FROM documents")).fetchall()
        ids = {r[0] for r in rows}
        log.info("DB has %d existing case_ids.", len(ids))
        return ids
    finally:
        session.close()


def scan_year(year: int, judgments_dir: Path, model_path: Path) -> None:
    from pipelines.optimizations import keyword_prefilter
    from pipelines.gst_classifier.classifier import GSTClassifier

    year_dir = judgments_dir / f"HC-GST-{year}"
    if not year_dir.exists():
        log.error("Directory not found: %s", year_dir)
        return

    tar_paths = sorted(year_dir.rglob("data.tar"))
    if not tar_paths:
        log.error("No data.tar files found under %s", year_dir)
        return

    log.info("Year %d — found %d tar archives.", year, len(tar_paths))

    clf = GSTClassifier(model_path)
    db_ids = load_db_case_ids()

    # Counters
    total       = 0   # all PDFs seen
    s1_pass     = 0   # passed keyword filter
    s2_pass     = 0   # passed classifier
    already_in  = 0   # in DB
    net_new     = 0   # genuinely new

    new_cases: list[dict] = []  # store for display

    for tar_idx, tar_path in enumerate(tar_paths):
        court_bench = f"{tar_path.parent.parent.name}/{tar_path.parent.name}"
        try:
            with tarfile.open(tar_path) as tf:
                members = [m for m in tf.getmembers()
                           if m.isfile() and m.name.lower().endswith(".pdf")]
                log.info("[%d/%d] %s — %d PDFs",
                         tar_idx + 1, len(tar_paths), court_bench, len(members))

                for member in members:
                    total += 1

                    # ── Stage 1: keyword filter ───────────────────────────
                    try:
                        f = tf.extractfile(member)
                        if f is None:
                            continue
                        pdf_bytes = f.read()
                    except Exception:
                        continue

                    text = _pdf_bytes_to_text(pdf_bytes, TEXT_CHARS)
                    if not text or len(text) < 100:
                        continue

                    hit, _ = keyword_prefilter(text)
                    if not hit:
                        continue
                    s1_pass += 1

                    # ── Stage 2: classifier ───────────────────────────────
                    if not clf.is_gst(text):
                        continue
                    s2_pass += 1

                    # ── Dedup ─────────────────────────────────────────────
                    cnr = _cnr_from_member(member.name)
                    if cnr in db_ids:
                        already_in += 1
                        continue

                    net_new += 1
                    new_cases.append({"cnr": cnr, "file": member.name.lstrip("./")} )

        except Exception as e:
            log.warning("Could not open %s: %s", tar_path, e)

        # Progress after each tar
        log.info("  → total=%d  s1_pass=%d  s2_pass=%d  in_db=%d  new=%d",
                 total, s1_pass, s2_pass, already_in, net_new)

    # ── Final report ──────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print(f"  SCAN REPORT — HC-GST-{year}")
    print("═" * 60)
    print(f"  Total PDFs scanned       : {total:>8,}")
    print(f"  After keyword filter (S1): {s1_pass:>8,}  ({s1_pass/max(total,1)*100:.1f}%)")
    print(f"  After classifier     (S2): {s2_pass:>8,}  ({s2_pass/max(total,1)*100:.1f}%)")
    print(f"  Already in DB            : {already_in:>8,}")
    print(f"  ── Net new cases ──────── : {net_new:>8,}")
    print("═" * 60)

    if new_cases:
        print(f"\n  First 20 net-new CNRs:")
        for c in new_cases[:20]:
            print(f"    {c['cnr']}")
        if len(new_cases) > 20:
            print(f"    … and {len(new_cases) - 20} more")

    # Save full new-case list to disk for reference
    out_path = Path(f".data/classifier/scan_{year}_new_cases.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for c in new_cases:
            f.write(f"{c['cnr']}\t{c['file']}\n")
    log.info("New case list saved → %s", out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan one HC year through GST filter pipeline")
    parser.add_argument("--year",          type=int,  required=True)
    parser.add_argument("--judgments-dir", type=Path, default=JUDGMENTS_DIR)
    parser.add_argument("--model-path",    type=Path, default=MODEL_PATH)
    args = parser.parse_args()

    scan_year(args.year, args.judgments_dir, args.model_path)


if __name__ == "__main__":
    main()
