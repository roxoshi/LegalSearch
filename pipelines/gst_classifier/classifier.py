"""
GST classifier inference wrapper.

Drop-in replacement for the NER-based filter in unified_pipeline.py.
Loads the trained sklearn pipeline from model.joblib and exposes is_gst().

Usage (standalone):
    from pipelines.gst_classifier.classifier import GSTClassifier
    clf = GSTClassifier()                          # loads default model
    clf.is_gst("...judgment text...")              # → True / False
    clf.predict_batch(["text1", "text2"])          # → [True, False]
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MODEL = Path(".data/classifier/model.joblib")
# Characters to feed the model — must match what prepare_data.py used
TEXT_CHARS = 6_000


class GSTClassifier:
    def __init__(self, model_path: Path = DEFAULT_MODEL) -> None:
        import joblib
        bundle = joblib.load(model_path)
        self._pipe      = bundle["pipeline"]
        self._threshold = bundle["threshold"]
        log.info("Loaded GSTClassifier from %s (threshold=%.2f)", model_path, self._threshold)

    def is_gst(self, text: str | None) -> bool:
        if not text or len(text.strip()) < 50:
            return False
        snippet = text[:TEXT_CHARS]
        prob = self._pipe.predict_proba([snippet])[0][1]
        return prob >= self._threshold

    def predict_batch(self, texts: list[str]) -> list[bool]:
        if not texts:
            return []
        snippets = [(t or "")[:TEXT_CHARS] for t in texts]
        probs = self._pipe.predict_proba(snippets)[:, 1]
        return [float(p) >= self._threshold for p in probs]

    def score(self, text: str) -> float:
        """Return raw probability (0–1) of being GST-related."""
        if not text:
            return 0.0
        return float(self._pipe.predict_proba([text[:TEXT_CHARS]])[0][1])
