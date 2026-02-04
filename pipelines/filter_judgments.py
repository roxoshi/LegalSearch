import argparse
import json
import logging
import os
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Tuple, Set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("filter_judgments")

FilterResult = namedtuple("FilterResult", ["kept", "removed"])

GST_STATUTES_KEYWORDS = ["goods and services", "goods & services", "gst"]

MAX_TEXT_CHARS = 6000

# Lazy-loaded NLP model (loaded on first use)
_nlp = None


def _load_nlp_model():
    """Load the spaCy NER model with GPU support if available."""
    global _nlp
    if _nlp is not None:
        return _nlp

    try:
        import torch
        import spacy

        # Enable GPU before loading the model
        if torch.cuda.is_available():
            logger.info("CUDA available, enabling GPU for spaCy")
            spacy.require_gpu()
        else:
            logger.info("CUDA not available, using CPU")

        # Try loading the model
        try:
            _nlp = spacy.load("en_legal_ner_trf")
            logger.info("Loaded spaCy model: en_legal_ner_trf")
        except OSError:
            # Model not installed as package, try importing directly
            import en_legal_ner_trf
            _nlp = en_legal_ner_trf.load()
            logger.info("Loaded spaCy model via direct import")

        return _nlp

    except ImportError as e:
        logger.warning(f"ML dependencies not available: {e}. Using fallback classifier.")
        return None


def predict_relevance(text: str) -> Tuple[Set[str], Set[str]]:
    """
    Extract legal entities from text using NER model.

    Returns:
        Tuple of (extracted_provisions, extracted_statutes)
    """
    nlp = _load_nlp_model()

    if nlp is None:
        # Fallback: simple keyword matching if ML model unavailable
        extracted_statutes = set()
        text_lower = text.lower()
        for kw in GST_STATUTES_KEYWORDS:
            if kw in text_lower:
                extracted_statutes.add(kw.upper())
        return (set(), extracted_statutes)

    extracted_provisions = set()
    extracted_statutes = set()

    doc = nlp(text)

    for ent in doc.ents:
        if ent.label_ == "PROVISION":
            extracted_provisions.add(ent.text.strip())
        elif ent.label_ == "STATUTE":
            extracted_statutes.add(ent.text.strip())

    return (extracted_provisions, extracted_statutes)


def is_gst_relevant(statutes: Set[str]) -> bool:
    """Check if any extracted statute mentions GST."""
    statutes_text = " ".join(statutes).lower()
    return any(kw in statutes_text for kw in GST_STATUTES_KEYWORDS)


def _classify_file(json_file: Path, pdf_path: Path, seed=None):
    """Classify a single JSON file and update it with extracted entities.

    Returns (json_file, stem, action, pdf_file, updated_data).
    action is one of: "keep", "remove"
    """
    stem = json_file.stem

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    text = (data.get("text_content") or "")[:MAX_TEXT_CHARS]
    extracted_provisions, extracted_statutes = predict_relevance(text)
    relevance = is_gst_relevant(extracted_statutes)

    # Add new fields to the data dict
    data['is_gst_core'] = relevance
    data['extracted_provisions'] = sorted(list(extracted_provisions))
    data['extracted_statutes'] = sorted(list(extracted_statutes))

    pdf_file = pdf_path / f"{stem}.pdf"
    action = "keep" if relevance else "remove"

    return json_file, stem, action, pdf_file, data


def filter_judgments(processed_dir, pdf_dir, seed=None, workers=None) -> FilterResult:
    """Walk processed JSON files and remove cases the classifier flags as irrelevant.

    Uses ML-based NER to extract legal entities and determine GST relevance.
    Updates JSON files with extracted entity information.
    Returns a FilterResult with counts of kept and removed cases.
    """
    kept = 0
    removed = 0

    processed_path = Path(processed_dir)
    pdf_path = Path(pdf_dir)

    json_files = sorted(processed_path.rglob("*.json"))

    if not json_files:
        logger.info("No JSON files found in %s", processed_dir)
        return FilterResult(kept=0, removed=0)

    # Note: With ML model, parallelization may not help much (GPU is the bottleneck)
    # Process sequentially for now to avoid GPU memory issues
    results = []
    for jf in json_files:
        result = _classify_file(jf, pdf_path, seed)
        results.append(result)

    # Phase 2: Apply updates and deletions
    for json_file, stem, action, pdf_file, updated_data in results:
        if action == "keep":
            # Save updated JSON with extracted entities
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(updated_data, f, indent=4, ensure_ascii=False)
            kept += 1
            logger.debug("KEEP: %s", stem)
        else:
            # Delete non-relevant cases
            json_file.unlink()
            if pdf_file and pdf_file.exists():
                pdf_file.unlink()
            removed += 1
            logger.debug("REMOVE: %s", stem)

    logger.info(
        "Filter complete: kept=%d, removed=%d",
        kept, removed,
    )
    return FilterResult(kept=kept, removed=removed)


def main():
    parser = argparse.ArgumentParser(
        description="Filter sharded judgments using ML classifier"
    )
    parser.add_argument(
        "--processed-dir",
        default=".data/metadata/processed",
        help="Directory containing processed JSON files",
    )
    parser.add_argument(
        "--pdf-dir",
        default=".data/gst_pdfs",
        help="Directory containing extracted PDF files",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed (unused, kept for CLI compatibility)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of workers (unused with ML model, kept for CLI compatibility)",
    )
    args = parser.parse_args()

    result = filter_judgments(
        processed_dir=args.processed_dir,
        pdf_dir=args.pdf_dir,
        seed=args.seed,
        workers=args.workers,
    )
    logger.info("Result: %s", result)


if __name__ == "__main__":
    main()
