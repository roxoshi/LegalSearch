"""
Build SC sample CSV with 6000-char text previews, matching the format of
scan_2023_sample_with_text.csv but for Supreme Court cases across all years.

Samples ~50 cases proportionally across years.

Output: .data/classifier/scan_sc_sample_with_text.csv
"""

from __future__ import annotations

import io
import logging
import random
import zipfile
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

JUDGMENTS_DIR = Path(".data/GST_judgments")
METADATA_DIR  = Path(".data/metadata/raw")
OUTPUT        = Path(".data/classifier/scan_sc_sample_with_text.csv")
TEXT_CHARS    = 6_000
SAMPLE_SIZE   = 50
YEARS         = list(range(2017, 2026))
RANDOM_SEED   = 42


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


def main() -> None:
    random.seed(RANDOM_SEED)

    # Load all SC parquet metadata
    frames = []
    for year in YEARS:
        pq = METADATA_DIR / f"SC-GST-{year}.parquet"
        if not pq.exists():
            continue
        df = pd.read_parquet(pq, columns=["case_id", "title", "judge", "decision_date", "path"])
        df["year"] = year
        frames.append(df)

    meta = pd.concat(frames, ignore_index=True)
    meta = meta.dropna(subset=["path"])
    meta["court"] = "Supreme Court of India"
    log.info("Total SC cases in metadata: %d", len(meta))

    # Sample proportionally across years
    per_year = max(1, SAMPLE_SIZE // len(YEARS))
    parts = []
    for year, group in meta.groupby("year"):
        parts.append(group.sample(min(len(group), per_year), random_state=RANDOM_SEED))
    sampled = pd.concat(parts).sample(frac=1, random_state=RANDOM_SEED).reset_index(drop=True)
    log.info("Sampled %d cases across %d years", len(sampled), sampled["year"].nunique())

    rows = []
    for _, row in sampled.iterrows():
        year      = int(row["year"])
        path_stem = str(row["path"]).strip()
        pdf_name  = f"{path_stem}_EN.pdf"
        zip_path  = JUDGMENTS_DIR / f"SC-GST-{year}.zip"

        if not zip_path.exists():
            continue

        try:
            with zipfile.ZipFile(zip_path) as zf:
                pdf_bytes = zf.read(pdf_name)
        except KeyError:
            log.warning("Not in zip: %s", pdf_name)
            continue
        except Exception as e:
            log.warning("Error reading %s: %s", pdf_name, e)
            continue

        text = _pdf_bytes_to_text(pdf_bytes, TEXT_CHARS)
        if not text:
            continue

        rows.append({
            "case_id":      row["case_id"],
            "title":        row["title"],
            "court":        row["court"],
            "decision_date": row["decision_date"],
            "judge":        row["judge"],
            "text_preview": text,
        })

    df_out = pd.DataFrame(rows)
    df_out.to_csv(OUTPUT, index=False)
    log.info("Saved %d rows → %s", len(df_out), OUTPUT)


if __name__ == "__main__":
    main()
