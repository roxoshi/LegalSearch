"""
Tier 1 Optimizations for LegalSearch Pipeline

1. Two-stage keyword pre-filter (skip NER for non-GST documents)
2. PyMuPDF text extraction (10-50x faster than pdfplumber)
3. ONNX Runtime embeddings (2-4x faster on CPU)
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    import numpy.typing as npt

# GST-related keywords for fast pre-filtering
# These are checked BEFORE expensive NER inference
GST_KEYWORDS: frozenset[str] = frozenset(
    [
        "goods and services",
        "goods & services"
    ]
)

# Regex patterns for more flexible matching
GST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bg\.?s\.?t\.?\b", re.IGNORECASE),  # G.S.T., GST, g.s.t
    re.compile(r"\bc\.?g\.?s\.?t\.?\b", re.IGNORECASE),  # CGST variants
    re.compile(r"\bs\.?g\.?s\.?t\.?\b", re.IGNORECASE),  # SGST variants
    re.compile(r"\bi\.?g\.?s\.?t\.?\b", re.IGNORECASE),  # IGST variants
    re.compile(r"goods\s+(?:and|&)\s+services?\s+tax", re.IGNORECASE),
    re.compile(r"section\s+\d+\s+of\s+(?:the\s+)?(?:c|s|i)?gst", re.IGNORECASE),
]


def keyword_prefilter(text: str | None) -> tuple[bool, set[str]]:
    """
    Fast keyword-based pre-filter to identify potential GST documents.

    This runs BEFORE expensive NER inference to filter out ~80% of documents
    that clearly don't relate to GST.

    Args:
        text: Document text content (first N characters is sufficient)

    Returns:
        Tuple of (is_potential_gst, matched_keywords)
        - is_potential_gst: True if document might be GST-related
        - matched_keywords: Set of keywords that matched (for debugging)
    """
    if not text:
        return False, set()

    text_lower = text.lower()
    matched: set[str] = set()

    # Check exact keyword matches
    for keyword in GST_KEYWORDS:
        if keyword in text_lower:
            matched.add(keyword)

    # Check regex patterns
    for pattern in GST_PATTERNS:
        match = pattern.search(text)
        if match:
            matched.add(match.group().lower())

    return len(matched) > 0, matched


def extract_text_pymupdf(pdf_bytes: bytes, max_chars: int = 6000) -> str:
    """
    Extract text from PDF using PyMuPDF (fitz).

    This is 10-50x faster than pdfplumber for text extraction.
    MuPDF warnings (corrupt xref, missing resources) are suppressed
    since they are non-fatal — text extraction still succeeds on
    the valid pages.

    Args:
        pdf_bytes: Raw PDF file bytes
        max_chars: Maximum characters to extract (for efficiency)

    Returns:
        Extracted text content
    """
    if not pdf_bytes:
        return ""

    try:
        import fitz  # PyMuPDF

        # Suppress noisy MuPDF warnings for malformed PDFs (syntax errors,
        # missing resources, bad xrefs). These are non-fatal — PyMuPDF still
        # extracts text from valid pages.
        fitz.TOOLS.mupdf_display_errors(False)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        content: list[str] = []
        total_len = 0

        for page in doc:
            text: str = page.get_text()
            if text:
                content.append(text)
                total_len += len(text)
                if total_len > max_chars:
                    break

        doc.close()
        return "\n\n".join(content).strip()[:max_chars]

    except Exception:
        return ""


def extract_text_pdfplumber(pdf_bytes: bytes, max_chars: int = 6000) -> str:
    """
    Extract text from PDF using pdfplumber (original implementation).

    Kept for comparison and fallback purposes.

    Args:
        pdf_bytes: Raw PDF file bytes
        max_chars: Maximum characters to extract

    Returns:
        Extracted text content
    """
    if not pdf_bytes:
        return ""

    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            content: list[str] = []
            total_len = 0
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    content.append(text)
                    total_len += len(text)
                    if total_len > max_chars:
                        break
            return "\n\n".join(content).strip()[:max_chars]

    except Exception:
        return ""


class ONNXEmbedder:
    """
    ONNX-optimized embedding generator.

    Uses ONNX Runtime for 2-4x faster CPU inference compared to PyTorch.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self.tokenizer: Any = None
        self.model: Any = None
        self._initialized: bool = False
        self._use_onnx: bool = False

    def _ensure_initialized(self) -> None:
        """Lazy initialization of ONNX model."""
        if self._initialized:
            return

        try:
            from optimum.onnxruntime import ORTModelForFeatureExtraction
            from transformers import AutoTokenizer

            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self.model = ORTModelForFeatureExtraction.from_pretrained(
                self.model_name,
                export=True,  # Export to ONNX on first use
            )
            self._initialized = True
            self._use_onnx = True

        except ImportError:
            # Fall back to regular transformers-based embedder
            from app.embeddings import EmbeddingModel  # type: ignore[no-redef]

            self.model = EmbeddingModel(self.model_name)
            self._initialized = True
            self._use_onnx = False

    def encode(
        self,
        texts: list[str],
        batch_size: int = 128,
        show_progress_bar: bool = False,
    ) -> list[list[float]]:
        """
        Encode texts to embeddings.

        Args:
            texts: List of text strings to encode
            batch_size: Batch size for encoding
            show_progress_bar: Whether to show progress

        Returns:
            List of embedding vectors
        """
        self._ensure_initialized()

        if not texts:
            return []

        if not self._use_onnx:
            # Fallback to SentenceTransformer
            embeddings = self.model.encode(
                texts, batch_size=batch_size, show_progress_bar=show_progress_bar
            )
            return embeddings.tolist()

        import numpy as np

        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]

            # Tokenize
            inputs = self.tokenizer(
                batch, padding=True, truncation=True, max_length=512, return_tensors="np"
            )

            # Run inference
            outputs = self.model(**inputs)

            # Mean pooling
            attention_mask: npt.NDArray[np.float64] = inputs["attention_mask"]
            token_embeddings: npt.NDArray[np.float64] = outputs.last_hidden_state

            input_mask_expanded = np.expand_dims(attention_mask, -1)
            sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
            sum_mask = np.clip(np.sum(input_mask_expanded, axis=1), a_min=1e-9, a_max=None)
            embeddings = sum_embeddings / sum_mask

            # Normalize
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / np.clip(norms, a_min=1e-9, a_max=None)

            all_embeddings.extend(embeddings.tolist())

        return all_embeddings


# Type variable for document objects
DocT = TypeVar("DocT")


class HasTextContent(Protocol):
    """Protocol for objects with text_content attribute."""

    text_content: str
    extracted_provisions: list[str]
    extracted_statutes: list[str]
    is_gst_core: bool | None


def two_stage_filter(
    docs: list[DocT],
    nlp: Any,
    text_getter: Callable[[DocT], str] | None = None,
    max_chars: int = 6000,
    ner_max_chars: int = 7000,
    ner_batch_size: int = 16,
) -> tuple[list[DocT], dict[str, Any]]:
    """
    Two-stage filtering: keyword pre-filter + NER.

    Stage 1: Fast keyword check (filters ~80% of non-GST docs)
    Stage 2: NER inference only on candidates

    Args:
        docs: List of document objects
        nlp: Loaded spaCy NER model
        text_getter: Function to get text from doc (default: doc.text_content)
        max_chars: Max chars for keyword pre-filter (cheap, can be large)
        ner_max_chars: Max chars for NER inference (transformer limit is
            512 tokens ≈ 2000 chars; keeping text short avoids truncation
            warnings and gives ~9x speedup vs 6000 chars due to O(n²) attention)
        ner_batch_size: Batch size for nlp.pipe() NER inference

    Returns:
        Tuple of (filtered_docs, stats_dict)
    """
    from tqdm import tqdm

    if text_getter is None:

        def default_getter(d: DocT) -> str:
            return str(getattr(d, "text_content", ""))[:max_chars]

        text_getter = default_getter

    # Stage 1: Keyword pre-filter (uses full max_chars — just string matching)
    candidates: list[DocT] = []
    rejected_early = 0

    for doc in tqdm(docs, desc="Keyword Pre-filter", unit="doc"):
        text = text_getter(doc)
        is_candidate, _ = keyword_prefilter(text)

        if is_candidate:
            candidates.append(doc)
        else:
            rejected_early += 1

    stats: dict[str, Any] = {
        "total": len(docs),
        "passed_keyword_filter": len(candidates),
        "rejected_by_keyword": rejected_early,
        "keyword_filter_rate": rejected_early / len(docs) if docs else 0,
    }

    if not candidates:
        stats["passed_ner"] = 0
        return [], stats

    # Stage 2: NER on candidates only, processed in chunks to bound memory.
    # Each chunk: build texts, run nlp.pipe(), classify, then free the
    # spaCy Doc objects before the next chunk. Without chunking, all texts
    # + all spaCy Docs are materialized at once, causing OOM on large datasets.
    NER_CHUNK_SIZE = 500

    gst_statutes_keywords = ["goods and services", "goods & services", "gst"]

    filtered: list[DocT] = []

    with tqdm(total=len(candidates), desc="NER Inference", unit="doc") as pbar:
        for chunk_start in range(0, len(candidates), NER_CHUNK_SIZE):
            chunk_docs = candidates[chunk_start : chunk_start + NER_CHUNK_SIZE]
            chunk_texts = [text_getter(doc)[:ner_max_chars] for doc in chunk_docs]

            for doc, spacy_doc in zip(
                chunk_docs,
                nlp.pipe(chunk_texts, batch_size=ner_batch_size, n_process=1),
            ):
                provisions: set[str] = set()
                statutes: set[str] = set()

                for ent in spacy_doc.ents:
                    if ent.label_ == "PROVISION":
                        provisions.add(ent.text.strip())
                    elif ent.label_ == "STATUTE":
                        statutes.add(ent.text.strip())

                doc.extracted_provisions = sorted(provisions)  # type: ignore[attr-defined]
                doc.extracted_statutes = sorted(statutes)  # type: ignore[attr-defined]

                statutes_text = " ".join(statutes).lower()
                doc.is_gst_core = any(kw in statutes_text for kw in gst_statutes_keywords)  # type: ignore[attr-defined]

                if doc.is_gst_core:  # type: ignore[attr-defined]
                    filtered.append(doc)

                pbar.update(1)

            # chunk_texts and spaCy Docs are freed here before next chunk
            del chunk_texts

    stats["passed_ner"] = len(filtered)
    stats["ner_filter_rate"] = (
        (len(candidates) - len(filtered)) / len(candidates) if candidates else 0
    )

    return filtered, stats
