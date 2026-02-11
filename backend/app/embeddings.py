"""Embedding model using plain HuggingFace transformers (no sentence-transformers dependency)."""

import logging
from typing import Union

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingModel:
    """Drop-in replacement for SentenceTransformer using plain transformers.

    Loads an AutoModel + AutoTokenizer and applies mean pooling to produce
    sentence embeddings. Automatically uses GPU if available.

    Usage is identical to SentenceTransformer:
        model = EmbeddingModel("sentence-transformers/all-MiniLM-L6-v2")
        vec = model.encode("hello world")           # 1-D numpy array
        vecs = model.encode(["hello", "world"])      # 2-D numpy array
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        import torch
        from transformers import AutoModel, AutoTokenizer

        logger.info(f"Loading embedding model: {model_name}")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        self.model.eval()
        logger.info(f"Embedding model loaded on {self.device}")

    def encode(
        self,
        sentences: Union[str, list[str]],
        batch_size: int = 128,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """Encode sentences into embeddings.

        Args:
            sentences: Single string or list of strings.
            batch_size: Batch size for encoding.
            show_progress_bar: Ignored (kept for API compatibility).

        Returns:
            numpy array — 1-D for single string input, 2-D for list input.
        """
        import torch
        import torch.nn.functional as F

        single_input = isinstance(sentences, str)
        if single_input:
            sentences = [sentences]

        all_embeddings: list[np.ndarray] = []

        for i in range(0, len(sentences), batch_size):
            batch = sentences[i : i + batch_size]
            inputs = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            ).to(self.device)

            with torch.no_grad():
                outputs = self.model(**inputs)

            # Mean pooling: average token embeddings weighted by attention mask
            attention_mask = inputs["attention_mask"]
            token_embeddings = outputs.last_hidden_state
            mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
            summed = torch.sum(token_embeddings * mask_expanded, dim=1)
            counts = torch.clamp(mask_expanded.sum(dim=1), min=1e-9)
            embeddings = F.normalize(summed / counts, p=2, dim=1)

            all_embeddings.append(embeddings.cpu().numpy())

        result = np.concatenate(all_embeddings, axis=0)

        if single_input:
            return result[0]  # Return 1-D array for single string (matches SentenceTransformer)
        return result
