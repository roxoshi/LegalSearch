"""Cross-encoder re-ranker using plain HuggingFace transformers (no sentence-transformers dependency)."""

import logging

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Score (query, passage) pairs for relevance re-ranking.

    Uses AutoModelForSequenceClassification — compatible with any cross-encoder
    on the Hub, e.g. cross-encoder/ms-marco-MiniLM-L-6-v2.

    Usage:
        reranker = CrossEncoderReranker("cross-encoder/ms-marco-MiniLM-L-6-v2")
        scores = reranker.rank("GST on freight", ["passage 1 ...", "passage 2 ..."])
        # Returns list[float] — higher = more relevant
    """

    def __init__(self, model_name: str):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        logger.info(f"Loading cross-encoder: {model_name}")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        self.model.eval()
        logger.info(f"Cross-encoder loaded on {self.device}")

    def rank(self, query: str, passages: list[str]) -> list[float]:
        """Return a relevance score for each passage (higher = more relevant)."""
        import torch

        if not passages:
            return []

        pairs = [[query, p] for p in passages]
        inputs = self.tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            logits = self.model(**inputs).logits

        # Single-output models (relevance score): squeeze to 1-D
        scores = logits.squeeze(-1).cpu().numpy()
        return scores.tolist()
