from pathlib import Path
from pipelines.gemini_batch_analyze import process_results

results_path = Path(".data/batch_analysis/batch_results.jsonl")
output_dir = Path(".data/batch_analysis")
success_dir = output_dir / "successes"
failure_dir = output_dir / "failures"
success_dir.mkdir(exist_ok=True)
failure_dir.mkdir(exist_ok=True)

# Build stem→path map from the JSONL keys (no PDFs needed for reprocessing)
import json
pdf_by_stem = {}
with results_path.open() as f:
    for line in f:
        if line.strip():
            record = json.loads(line)
            key = record.get("key", "")
            if key:
                pdf_by_stem[key] = Path(f"{key}.pdf")

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
stats = process_results(results_path.read_text(), pdf_by_stem, success_dir, failure_dir)
print(f"Done — {stats.succeeded} succeeded, {stats.failed} failed")
