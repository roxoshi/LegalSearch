#!/usr/bin/env python3
"""
Download the legal NER spaCy model from HuggingFace and fix any JSON issues.

Works in both Docker (/app/models) and local dev (.data/models) environments.
Usage:
    uv run python scripts/install_spacy_model.py          # local dev
    python scripts/install_spacy_model.py                  # Docker build
"""

import json
import os
import re
import sys
from pathlib import Path

# Docker uses /app/models; local dev uses .data/models relative to project root
if os.path.isdir("/app/models") or not os.path.isfile("pyproject.toml"):
    MODEL_DIR = "/app/models/en_legal_ner_trf"
else:
    MODEL_DIR = str(Path(__file__).resolve().parent.parent / ".data" / "models" / "en_legal_ner_trf")


def fix_json(content):
    """Fix common JSON syntax errors like trailing commas."""
    # Remove trailing commas before } or ]
    content = re.sub(r",(\s*[}\]])", r"\1", content)
    # Remove any BOM or weird characters at start
    content = content.lstrip("\ufeff\xef\xbb\xbf")
    return content


def download_model():
    from huggingface_hub import snapshot_download

    repo_id = "opennyaiorg/en_legal_ner_trf"

    print(f"Downloading {repo_id} from HuggingFace...")

    os.makedirs(MODEL_DIR, exist_ok=True)

    # Download all files
    snapshot_download(
        repo_id=repo_id,
        local_dir=MODEL_DIR,
    )

    print(f"Downloaded to {MODEL_DIR}")

    # Fix meta.json if needed
    meta_path = os.path.join(MODEL_DIR, "meta.json")
    if os.path.exists(meta_path):
        print("Checking meta.json...")
        with open(meta_path, encoding="utf-8") as f:
            content = f.read()

        try:
            meta = json.loads(content)
            print(f"meta.json is valid: {meta.get('name', 'unknown')}")
        except json.JSONDecodeError as e:
            print(f"meta.json has error: {e}")
            print("Attempting to fix...")

            # Try to fix common issues
            fixed_content = fix_json(content)

            try:
                meta = json.loads(fixed_content)
                # Write fixed version
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)
                print("Fixed and saved meta.json")
            except json.JSONDecodeError:
                print("Could not auto-fix, creating new meta.json from config...")
                # Create minimal meta.json based on typical spaCy model
                meta = {
                    "lang": "en",
                    "name": "legal_ner_trf",
                    "version": "3.2.0",
                    "spacy_version": ">=3.2.0,<4.0.0",
                    "description": "Indian Legal Named Entity Recognition",
                    "author": "OpenNyAI",
                    "email": "",
                    "url": "https://huggingface.co/opennyaiorg/en_legal_ner_trf",
                    "license": "MIT",
                    "pipeline": ["transformer", "ner"],
                    "components": ["transformer", "ner"],
                    "labels": {
                        "ner": [
                            "COURT",
                            "PETITIONER",
                            "RESPONDENT",
                            "JUDGE",
                            "LAWYER",
                            "DATE",
                            "ORG",
                            "GPE",
                            "STATUTE",
                            "PROVISION",
                            "PRECEDENT",
                            "CASE_NUMBER",
                            "WITNESS",
                        ]
                    },
                }
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)
                print("Created new meta.json")

    # Verify model loads
    print("Verifying model...")
    import spacy

    nlp = spacy.load(MODEL_DIR)
    print(f"SUCCESS: Model loaded with pipeline: {nlp.pipe_names}")


if __name__ == "__main__":
    download_model()
