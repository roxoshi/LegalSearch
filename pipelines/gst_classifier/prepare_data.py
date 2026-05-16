"""
Prepare training data for the GST binary classifier.

Positives  (label=1): PDFs in .data/gst_pdfs/ — confirmed GST judgments already in DB.
Negatives  (label=0): PDFs from raw HC tars + SC zips that FAIL the keyword prefilter.
                       Keyword rejects have zero GST signals — cleanest possible negatives.

Text budget: TEXT_CHARS from each document (same for both classes).
Negatives split ~50/50 between SC zips and HC tars (SC exhausted first, HC fills the rest).

Usage:
    uv run python -m pipelines.gst_classifier.prepare_data
    uv run python -m pipelines.gst_classifier.prepare_data --neg-target 21000
    uv run python -m pipelines.gst_classifier.prepare_data --out-dir .data/classifier
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import random
import tarfile
import zipfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

GST_JUDGMENTS_DIR = Path(".data/GST_judgments")
GST_PDFS_DIR      = Path(".data/gst_pdfs")
DEFAULT_OUT        = Path(".data/classifier")
TEXT_CHARS         = 6_000   # same ceiling for both classes
NEG_TARGET         = 21_000
SEED               = 42


# ── PDF → text ────────────────────────────────────────────────────────────────

def _pdf_bytes_to_text(pdf_bytes: bytes, max_chars: int) -> str | None:
    """Extract up to max_chars of text from raw PDF bytes using PyMuPDF."""
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
        return "".join(parts)[:max_chars].strip() or None
    except Exception:
        return None


def _pdf_file_to_text(path: Path, max_chars: int) -> str | None:
    """Extract up to max_chars of text from a PDF file."""
    try:
        import fitz
        fitz.TOOLS.mupdf_display_errors(False)
        doc = fitz.open(str(path))
        parts: list[str] = []
        total = 0
        for page in doc:
            t = page.get_text()
            parts.append(t)
            total += len(t)
            if total >= max_chars:
                break
        return "".join(parts)[:max_chars].strip() or None
    except Exception:
        return None


# ── Keyword filter ────────────────────────────────────────────────────────────

def _is_gst(text: str) -> bool:
    from pipelines.optimizations import keyword_prefilter
    hit, _ = keyword_prefilter(text)
    return hit


# ── Positives ─────────────────────────────────────────────────────────────────

def collect_positives(pdf_dir: Path, text_chars: int) -> list[dict]:
    try:
        import fitz
        fitz.TOOLS.mupdf_display_errors(False)
    except ImportError:
        raise RuntimeError("PyMuPDF not installed — run: uv add pymupdf")

    records: list[dict] = []
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    log.info("Extracting text from %d positive PDFs in %s…", len(pdfs), pdf_dir)

    for i, pdf_path in enumerate(pdfs):
        if i % 1000 == 0:
            log.info("  positives: %d / %d", i, len(pdfs))
        text = _pdf_file_to_text(pdf_path, text_chars)
        if not text or len(text) < 100:
            continue
        records.append({"text": text, "label": 1, "source": pdf_path.stem})

    log.info("Collected %d positives.", len(records))
    return records


# ── Negatives: SC zips ────────────────────────────────────────────────────────

def collect_sc_negatives(
    judgments_dir: Path,
    target: int,
    text_chars: int,
    rng: random.Random,
) -> list[dict]:
    """Stream all SC zips, take keyword-filter rejects as negatives."""
    zips = sorted(judgments_dir.glob("SC-GST-*.zip"))
    rng.shuffle(zips)

    records: list[dict] = []
    log.info("Scanning %d SC zips for negatives (target=%d)…", len(zips), target)

    for zip_path in zips:
        if len(records) >= target:
            break
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
                rng.shuffle(names)
                before = len(records)
                for name in names:
                    if len(records) >= target:
                        break
                    try:
                        pdf_bytes = zf.read(name)
                    except Exception:
                        continue
                    text = _pdf_bytes_to_text(pdf_bytes, text_chars)
                    if not text or len(text) < 100:
                        continue
                    if _is_gst(text):
                        continue  # GST hit → not a clean negative
                    records.append({"text": text, "label": 0, "source": f"SC:{zip_path.stem}/{name}"})
                log.info("  %s → +%d negatives (total %d)", zip_path.name, len(records) - before, len(records))
        except Exception as e:
            log.warning("Could not open %s: %s", zip_path.name, e)

    log.info("SC negatives collected: %d", len(records))
    return records


# ── Negatives: HC tars ────────────────────────────────────────────────────────

def collect_hc_negatives(
    judgments_dir: Path,
    target: int,
    text_chars: int,
    rng: random.Random,
) -> list[dict]:
    """Stream HC data.tar archives, take keyword-filter rejects as negatives.

    Spreads sampling across ALL tars with a per-tar cap so no single court
    dominates the negative class.
    """
    tar_paths = sorted(judgments_dir.rglob("data.tar"))
    rng.shuffle(tar_paths)
    n_tars = len(tar_paths)

    # Cap per tar: ceiling of target/n_tars so we always spread across courts.
    # We do two passes — first pass collects up to cap per tar, second pass
    # fills any shortfall from tars that had more to give.
    per_tar_cap = max(50, -(-target // n_tars))  # ceiling division
    log.info(
        "Scanning %d HC tar files for negatives (target=%d, cap=%d/tar)…",
        n_tars, target, per_tar_cap,
    )

    records: list[dict] = []

    for tar_path in tar_paths:
        if len(records) >= target:
            break
        remaining = target - len(records)
        cap = min(per_tar_cap, remaining)
        try:
            with tarfile.open(tar_path) as tf:
                members = [m for m in tf.getmembers()
                           if m.isfile() and m.name.lower().endswith(".pdf")]
                rng.shuffle(members)
                before = len(records)
                collected = 0
                for member in members:
                    if collected >= cap:
                        break
                    try:
                        f = tf.extractfile(member)
                        if f is None:
                            continue
                        pdf_bytes = f.read()
                    except Exception:
                        continue
                    text = _pdf_bytes_to_text(pdf_bytes, text_chars)
                    if not text or len(text) < 100:
                        continue
                    if _is_gst(text):
                        continue  # GST hit → not a clean negative
                    records.append({"text": text, "label": 0,
                                    "source": f"HC:{tar_path.parent.name}/{member.name}"})
                    collected += 1
                log.info("  %s → +%d negatives (total %d)",
                         tar_path.parent.name, len(records) - before, len(records))
        except Exception as e:
            log.warning("Could not open %s: %s", tar_path, e)

    log.info("HC negatives collected: %d across %d tars", len(records), n_tars)
    return records


# ── Split & save ──────────────────────────────────────────────────────────────

def split_and_save(
    positives: list[dict],
    negatives: list[dict],
    out_dir: Path,
    rng: random.Random,
) -> None:
    rng.shuffle(positives)
    rng.shuffle(negatives)

    def _split(items: list[dict]) -> tuple[list, list, list]:
        n = len(items)
        n_val  = max(1, int(n * 0.10))
        n_test = max(1, int(n * 0.10))
        train  = items[n_val + n_test:]
        val    = items[:n_val]
        test   = items[n_val: n_val + n_test]
        return train, val, test

    pos_train, pos_val, pos_test = _split(positives)
    neg_train, neg_val, neg_test = _split(negatives)

    splits = {
        "train": pos_train + neg_train,
        "val":   pos_val   + neg_val,
        "test":  pos_test  + neg_test,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, records in splits.items():
        rng.shuffle(records)
        path = out_dir / f"{name}.jsonl"
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        n_pos = sum(1 for r in records if r["label"] == 1)
        n_neg = sum(1 for r in records if r["label"] == 0)
        log.info("  %-6s %5d total  (%d pos / %d neg) → %s", name, len(records), n_pos, n_neg, path)

    stats = {
        "positives": len(positives),
        "negatives": len(negatives),
        "text_chars": TEXT_CHARS,
        "splits": {k: len(v) for k, v in splits.items()},
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    log.info("Stats saved → %s/stats.json", out_dir)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare GST classifier training data")
    parser.add_argument("--gst-pdfs-dir",    type=Path, default=GST_PDFS_DIR)
    parser.add_argument("--judgments-dir",   type=Path, default=GST_JUDGMENTS_DIR)
    parser.add_argument("--out-dir",         type=Path, default=DEFAULT_OUT)
    parser.add_argument("--neg-target",      type=int,  default=NEG_TARGET,
                        help="Total negatives to collect (default: 21000)")
    parser.add_argument("--text-chars",      type=int,  default=TEXT_CHARS,
                        help="Characters to keep per document (default: 6000)")
    parser.add_argument("--seed",            type=int,  default=SEED)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    # ── Positives ─────────────────────────────────────────────────────────────
    positives = collect_positives(args.gst_pdfs_dir, args.text_chars)

    # ── Negatives: SC first, HC fills the rest ────────────────────────────────
    sc_target = args.neg_target // 2
    sc_negs   = collect_sc_negatives(args.judgments_dir, sc_target, args.text_chars, rng)

    hc_target = args.neg_target - len(sc_negs)
    hc_negs   = collect_hc_negatives(args.judgments_dir, hc_target, args.text_chars, rng)

    negatives = sc_negs + hc_negs
    log.info("Total negatives: %d  (SC=%d, HC=%d)", len(negatives), len(sc_negs), len(hc_negs))

    # ── Split & save ──────────────────────────────────────────────────────────
    split_and_save(positives, negatives, args.out_dir, rng)
    log.info("Done. Data written to %s", args.out_dir)


if __name__ == "__main__":
    main()
