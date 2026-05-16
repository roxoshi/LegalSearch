"""
Train a binary GST/non-GST classifier (TF-IDF + Logistic Regression).

Reads train/val/test JSONL files produced by prepare_data.py and outputs:
  model.joblib   — sklearn Pipeline (TfidfVectorizer → LogisticRegression) + threshold
  eval.json      — precision, recall, F1, confusion matrix, false-negative rate
  features.json  — top positive/negative feature weights for inspection

Optimised for recall: missing a GST case is worse than letting a non-GST through.
The threshold sweep finds the point that maximises F1 on val set; you can
push it lower (e.g. --threshold 0.25) to trade precision for more recall.

Usage:
    uv run python -m pipelines.gst_classifier.train
    uv run python -m pipelines.gst_classifier.train --threshold 0.30
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import joblib
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_DATA  = Path(".data/classifier")
DEFAULT_MODEL = Path(".data/classifier/model.joblib")


def _load_jsonl(path: Path) -> tuple[list[str], list[int]]:
    texts, labels = [], []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            texts.append(r["text"])
            labels.append(r["label"])
    return texts, labels


def train(
    data_dir: Path,
    model_path: Path,
    threshold_override: float | None,
    max_features: int,
    C: float,
) -> None:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        classification_report,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )
    from sklearn.pipeline import Pipeline

    # ── Load ──────────────────────────────────────────────────────────────────
    log.info("Loading data from %s", data_dir)
    X_train, y_train = _load_jsonl(data_dir / "train.jsonl")
    X_val,   y_val   = _load_jsonl(data_dir / "val.jsonl")
    X_test,  y_test  = _load_jsonl(data_dir / "test.jsonl")
    log.info("train=%d  val=%d  test=%d", len(X_train), len(X_val), len(X_test))

    n_pos = sum(y_train)
    n_neg = len(y_train) - n_pos
    log.info("Train class balance — GST: %d  non-GST: %d  (ratio %.1f×)", n_pos, n_neg, n_neg / max(n_pos, 1))

    # ── Model ─────────────────────────────────────────────────────────────────
    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=max_features,
            sublinear_tf=True,         # log(1 + tf) — reduces outlier terms
            strip_accents="unicode",
            analyzer="word",
            # Allow legal tokens: "CGST", "section", "s.168", "e-way"
            token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9.&/\-]{1,}\b",
            min_df=3,                  # ignore very rare terms
        )),
        ("clf", LogisticRegression(
            C=C,
            class_weight="balanced",   # upweights minority class → recall-biased
            solver="lbfgs",
            max_iter=1000,
            n_jobs=-1,
        )),
    ])

    log.info("Training (max_features=%d, C=%.2f)…", max_features, C)
    pipe.fit(X_train, y_train)
    log.info("Training complete.")

    # ── Threshold sweep on val set ────────────────────────────────────────────
    val_probs = pipe.predict_proba(X_val)[:, 1]
    log.info("Threshold sweep on val set (lower threshold = higher recall):")
    log.info("  %-6s  %-8s  %-10s  %-8s  %-8s", "thresh", "F1", "precision", "recall", "FNR%")

    best_thresh = 0.5
    best_f1 = 0.0
    for t in np.arange(0.15, 0.70, 0.05):
        preds = (val_probs >= t).astype(int)
        f1    = f1_score(y_val, preds, zero_division=0)
        prec  = precision_score(y_val, preds, zero_division=0)
        rec   = recall_score(y_val, preds, zero_division=0)
        cm    = confusion_matrix(y_val, preds)
        fn    = cm[1][0] if cm.shape == (2, 2) else 0
        tp    = cm[1][1] if cm.shape == (2, 2) else 0
        fnr   = fn / max(fn + tp, 1) * 100
        flag  = " ◄" if f1 > best_f1 else ""
        log.info("  %.2f    %.4f    %.4f      %.4f    %.1f%%%s", t, f1, prec, rec, fnr, flag)
        if f1 > best_f1:
            best_f1 = f1
            best_thresh = round(float(t), 2)

    chosen = threshold_override if threshold_override is not None else best_thresh
    log.info("Chosen threshold: %.2f%s", chosen,
             " (manual override)" if threshold_override is not None else " (auto, best val F1)")

    # ── Evaluate on held-out test set ─────────────────────────────────────────
    test_probs = pipe.predict_proba(X_test)[:, 1]
    test_preds = (test_probs >= chosen).astype(int)

    log.info("\nTest set results:\n%s",
             classification_report(y_test, test_preds, target_names=["non-GST", "GST"]))

    cm = confusion_matrix(y_test, test_preds)
    tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (0, 0, 0, 0)
    fnr = fn / max(fn + tp, 1)
    log.info("Confusion matrix:  TN=%d  FP=%d  FN=%d  TP=%d", tn, fp, fn, tp)
    log.info("False Negative Rate (GST cases missed): %.2f%%  — target <5%%", fnr * 100)

    report = classification_report(y_test, test_preds, target_names=["non-GST", "GST"], output_dict=True)

    # ── Top features ──────────────────────────────────────────────────────────
    feat_names = pipe.named_steps["tfidf"].get_feature_names_out()
    coefs      = pipe.named_steps["clf"].coef_[0]
    top_gst    = feat_names[np.argsort(coefs)[-50:][::-1]].tolist()
    top_nongst = feat_names[np.argsort(coefs)[:50]].tolist()

    log.info("Top GST features:     %s", top_gst[:20])
    log.info("Top non-GST features: %s", top_nongst[:20])

    # ── Save ──────────────────────────────────────────────────────────────────
    model_path.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump({"pipeline": pipe, "threshold": chosen}, model_path)
    log.info("Model saved → %s", model_path)

    eval_out = {
        "threshold": chosen,
        "test": {
            "gst_precision":       report["GST"]["precision"],
            "gst_recall":          report["GST"]["recall"],
            "gst_f1":              report["GST"]["f1-score"],
            "false_negative_rate": fnr,
            "confusion_matrix":    cm.tolist(),
        },
    }
    (model_path.parent / "eval.json").write_text(json.dumps(eval_out, indent=2))

    features_out = {"top_gst": top_gst, "top_nongst": top_nongst}
    (model_path.parent / "features.json").write_text(json.dumps(features_out, indent=2))

    log.info("Eval + features saved to %s/", model_path.parent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train GST binary classifier")
    parser.add_argument("--data-dir",     type=Path,  default=DEFAULT_DATA)
    parser.add_argument("--model-path",   type=Path,  default=DEFAULT_MODEL)
    parser.add_argument("--threshold",    type=float, default=None,
                        help="Override auto-selected threshold (lower = higher recall)")
    parser.add_argument("--max-features", type=int,   default=60_000)
    parser.add_argument("--C",            type=float, default=1.0)
    args = parser.parse_args()

    train(args.data_dir, args.model_path, args.threshold, args.max_features, args.C)


if __name__ == "__main__":
    main()
