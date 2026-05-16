"""Download and process OpenAI Batch API results into analysis JSON files.

For each successful result:
  - Validates JSON against CaseLawAnalysis schema
  - Saves as successes/{primary_cnr}.json
  - Copies to all duplicate CNRs sharing the same PDF (from dedup_map.json)

Usage:
    OPENAI_API_KEY=sk-... uv run python -m pipelines.process_openai_batch --batch-id batch_abc123
    OPENAI_API_KEY=sk-... uv run python -m pipelines.process_openai_batch --results-file /path/to/results.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pipelines.llm_analyze import CaseLawAnalysis, parse_json_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("process_openai_batch")

OUTPUT_DIR = Path(".data/batch_analysis")


def download_results(batch_id: str) -> Path:
    from openai import OpenAI
    client = OpenAI()

    batch = client.batches.retrieve(batch_id)
    if batch.status != "completed":
        logger.error("Batch %s is not completed yet (status=%s)", batch_id, batch.status)
        sys.exit(1)

    if not batch.output_file_id:
        logger.error("No output file for batch %s", batch_id)
        sys.exit(1)

    out_path = OUTPUT_DIR / f"openai_batch_results_{batch_id}.jsonl"
    logger.info("Downloading output file %s...", batch.output_file_id)
    content = client.files.content(batch.output_file_id)
    out_path.write_bytes(content.read())
    logger.info("Saved to %s", out_path)
    return out_path


def process_results(results_path: Path, output_dir: Path) -> None:
    successes_dir = output_dir / "successes"
    failures_dir = output_dir / "failures"
    successes_dir.mkdir(parents=True, exist_ok=True)
    failures_dir.mkdir(parents=True, exist_ok=True)

    dedup_map_path = output_dir / "dedup_map.json"
    dedup_map: dict[str, list[str]] = {}
    if dedup_map_path.exists():
        dedup_map = json.loads(dedup_map_path.read_text())
        logger.info("Loaded dedup_map.json (%d primary CNRs)", len(dedup_map))
    else:
        logger.warning("dedup_map.json not found — duplicates won't be expanded")

    succeeded = 0
    failed = 0
    duplicates_written = 0

    with results_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            row = json.loads(line)
            custom_id: str = row["custom_id"]  # primary CNR
            error = row.get("error")
            response = row.get("response", {})
            status_code = response.get("status_code", 0)

            # --- failure path ---
            if error or status_code != 200:
                detail = json.dumps(error or response.get("body", {}))
                fail_path = failures_dir / f"{custom_id}_api_error.log"
                fail_path.write_text(
                    json.dumps({"custom_id": custom_id, "error": detail}, indent=2),
                    encoding="utf-8",
                )
                logger.warning("API error for %s: %s", custom_id, detail[:120])
                failed += 1
                continue

            # --- parse + validate ---
            raw = response["body"]["choices"][0]["message"]["content"]
            try:
                data = parse_json_response(raw)
                analysis = CaseLawAnalysis(**data)
            except Exception as exc:
                fail_path = failures_dir / f"{custom_id}_parse_error.log"
                fail_path.write_text(
                    json.dumps({"custom_id": custom_id, "error": str(exc), "raw": raw}, indent=2),
                    encoding="utf-8",
                )
                logger.warning("Parse/validation error for %s: %s", custom_id, exc)
                failed += 1
                continue

            # --- write primary ---
            analysis_json = analysis.model_dump_json(indent=2)
            (successes_dir / f"{custom_id}.json").write_text(analysis_json, encoding="utf-8")
            succeeded += 1

            # --- expand duplicates ---
            siblings = dedup_map.get(custom_id, [custom_id])
            for sibling_cnr in siblings:
                if sibling_cnr == custom_id:
                    continue
                sibling_path = successes_dir / f"{sibling_cnr}.json"
                if not sibling_path.exists():
                    sibling_path.write_text(analysis_json, encoding="utf-8")
                    duplicates_written += 1

    total_written = succeeded + duplicates_written
    logger.info(
        "Done — succeeded=%d  duplicates_expanded=%d  total_json_files=%d  failed=%d",
        succeeded, duplicates_written, total_written, failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Process OpenAI Batch API results")
    parser.add_argument("--batch-id", nargs="+", help="OpenAI batch ID(s) to download and process")
    parser.add_argument("--results-file", nargs="+", help="Path(s) to already-downloaded results JSONL")
    parser.add_argument("--all", action="store_true", help="Process all saved batch IDs from openai_batch_ids.txt")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    results_paths: list[Path] = []

    if args.results_file:
        results_paths = [Path(p) for p in args.results_file]
    elif args.batch_id:
        results_paths = [download_results(bid) for bid in args.batch_id]
    elif args.all:
        ids_file = output_dir / "openai_batch_ids.txt"
        if not ids_file.exists():
            logger.error("No saved batch IDs at %s", ids_file)
            sys.exit(1)
        ids = [l.strip() for l in ids_file.read_text().splitlines() if l.strip()]
        results_paths = [download_results(bid) for bid in ids]
    else:
        logger.error("Provide --batch-id, --results-file, or --all")
        sys.exit(1)

    for results_path in results_paths:
        logger.info("Processing %s...", results_path.name)
        process_results(results_path, output_dir)


if __name__ == "__main__":
    main()
