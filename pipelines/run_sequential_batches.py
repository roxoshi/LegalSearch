"""Sequential OpenAI Batch API runner for Tier 1 accounts.

Submits one small batch at a time, waits for completion, processes results,
then moves to the next. Fully resumable — skips CNRs already in successes dir.

Tier 1 gpt-4o-mini limit: 2M enqueued tokens.
Batch size: 200 requests (~1.35M tokens) — safely under limit.

Usage:
    set -a && source envs/.env.staging && set +a
    uv run python -m pipelines.run_sequential_batches

    # Resume after interruption (automatically skips completed CNRs):
    uv run python -m pipelines.run_sequential_batches
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from pipelines.llm_analyze import CaseLawAnalysis, parse_json_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(".data/logs/sequential_batch.log"),
    ],
)
logger = logging.getLogger("sequential_batch")

JSONL_PATH = Path(".data/batch_analysis/openai_batch_requests.jsonl")
OUTPUT_DIR = Path(".data/batch_analysis")
BATCH_SIZE = 200          # requests per batch (~1.35M tokens, under 2M limit)
POLL_INTERVAL = 60        # seconds between status checks
STUCK_TIMEOUT = 90 * 60  # cancel + retry if no progress for this many seconds
MODEL = "gpt-4o-mini"


def load_completed_cnrs(successes_dir: Path) -> set[str]:
    return {p.stem for p in successes_dir.glob("*.json")}


def load_pending_lines(jsonl_path: Path, completed: set[str]) -> list[str]:
    """Return JSONL lines whose custom_id is not yet completed."""
    pending = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            custom_id = json.loads(line)["custom_id"]
            if custom_id not in completed:
                pending.append(line)
    return pending


def submit_batch(lines: list[str], client) -> str:
    """Upload lines as a JSONL file and create a batch job. Returns batch ID."""
    import io
    content = "\n".join(lines) + "\n"
    file_obj = client.files.create(
        file=("batch.jsonl", io.BytesIO(content.encode()), "application/jsonl"),
        purpose="batch",
    )
    batch = client.batches.create(
        input_file_id=file_obj.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    return batch.id


def wait_for_completion(batch_id: str, client) -> str:
    """Poll until batch is completed/failed/cancelled. Returns final status.

    Cancels and returns 'cancelled' if no progress for STUCK_TIMEOUT seconds.
    """
    last_completed = -1
    last_progress_time = time.time()

    while True:
        batch = client.batches.retrieve(batch_id)
        status = batch.status
        rc = batch.request_counts
        completed = rc.completed if rc else 0
        logger.info(
            "  Batch %s  status=%s  completed=%s/%s  failed=%s",
            batch_id[:24], status,
            completed,
            rc.total if rc else "?",
            rc.failed if rc else "?",
        )
        if status in ("completed", "failed", "cancelled", "expired"):
            return status

        if completed != last_completed:
            last_completed = completed
            last_progress_time = time.time()

        stuck_for = time.time() - last_progress_time
        if stuck_for >= STUCK_TIMEOUT:
            logger.warning(
                "  Batch %s stuck at %s/%s for %.0f min — cancelling",
                batch_id[:24], completed, rc.total if rc else "?", stuck_for / 60,
            )
            try:
                client.batches.cancel(batch_id)
            except Exception as exc:
                logger.warning("  Cancel request failed: %s", exc)
            return "cancelled"

        time.sleep(POLL_INTERVAL)


def process_batch_results(batch_id: str, client, output_dir: Path, dedup_map: dict) -> tuple[int, int]:
    """Download results and write JSON files. Returns (succeeded, failed)."""
    batch = client.batches.retrieve(batch_id)
    if not batch.output_file_id:
        logger.warning("No output file for batch %s", batch_id)
        return 0, 0

    successes_dir = output_dir / "successes"
    failures_dir = output_dir / "failures"

    content = client.files.content(batch.output_file_id).read().decode("utf-8")
    succeeded = 0
    failed = 0

    for line in content.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        custom_id = row["custom_id"]
        error = row.get("error")
        response = row.get("response", {})

        if error or response.get("status_code", 0) != 200:
            (failures_dir / f"{custom_id}_api_error.log").write_text(
                json.dumps({"custom_id": custom_id, "error": str(error or response)}, indent=2)
            )
            failed += 1
            continue

        raw = response["body"]["choices"][0]["message"]["content"]
        try:
            data = parse_json_response(raw)
            analysis = CaseLawAnalysis(**data)
            analysis_json = analysis.model_dump_json(indent=2)

            (successes_dir / f"{custom_id}.json").write_text(analysis_json, encoding="utf-8")
            succeeded += 1

            for sibling in dedup_map.get(custom_id, []):
                if sibling != custom_id:
                    sib_path = successes_dir / f"{sibling}.json"
                    if not sib_path.exists():
                        sib_path.write_text(analysis_json, encoding="utf-8")
        except Exception as exc:
            (failures_dir / f"{custom_id}_parse_error.log").write_text(
                json.dumps({"custom_id": custom_id, "error": str(exc), "raw": raw}, indent=2)
            )
            failed += 1

    return succeeded, failed


def main() -> None:
    from openai import OpenAI
    client = OpenAI()

    output_dir = OUTPUT_DIR
    successes_dir = output_dir / "successes"
    failures_dir = output_dir / "failures"
    successes_dir.mkdir(parents=True, exist_ok=True)
    failures_dir.mkdir(parents=True, exist_ok=True)

    # Load dedup map
    dedup_map_path = output_dir / "dedup_map.json"
    dedup_map = json.loads(dedup_map_path.read_text()) if dedup_map_path.exists() else {}

    # Discover pending work
    completed = load_completed_cnrs(successes_dir)
    logger.info("Already completed: %d CNRs", len(completed))

    pending_lines = load_pending_lines(JSONL_PATH, completed)
    total_pending = len(pending_lines)
    logger.info("Pending requests: %d", total_pending)

    if not total_pending:
        logger.info("Nothing to do — all CNRs already processed.")
        return

    total_batches = (total_pending + BATCH_SIZE - 1) // BATCH_SIZE
    logger.info("Will submit %d batches of up to %d requests each", total_batches, BATCH_SIZE)

    total_succeeded = 0
    total_failed = 0
    start_time = time.time()

    for batch_num, offset in enumerate(range(0, total_pending, BATCH_SIZE), 1):
        chunk = pending_lines[offset: offset + BATCH_SIZE]
        elapsed = time.time() - start_time
        if batch_num > 1:
            avg_sec = elapsed / (batch_num - 1)
            remaining = (total_batches - batch_num + 1) * avg_sec
            eta = datetime.now() + timedelta(seconds=remaining)
            logger.info(
                "Batch %d/%d  (%d requests)  ETA: %s",
                batch_num, total_batches, len(chunk),
                eta.strftime("%Y-%m-%d %H:%M"),
            )
        else:
            logger.info("Batch %d/%d  (%d requests)", batch_num, total_batches, len(chunk))

        # Submit
        try:
            batch_id = submit_batch(chunk, client)
            logger.info("  Submitted: %s", batch_id)
        except Exception as exc:
            logger.error("  Failed to submit batch %d: %s", batch_num, exc)
            time.sleep(60)
            continue

        # Wait
        status = wait_for_completion(batch_id, client)
        if status in ("failed", "expired"):
            logger.error("  Batch %s ended with status=%s — skipping", batch_id, status)
            continue

        # Process (works for both 'completed' and 'cancelled' — partial output file may exist)
        s, f = process_batch_results(batch_id, client, output_dir, dedup_map)
        total_succeeded += s
        total_failed += f
        logger.info("  Processed: %d succeeded  %d failed  (running total: %d/%d)", s, f, total_succeeded + total_failed, total_pending)

        if status == "cancelled":
            # Re-queue the CNRs that didn't make it into this batch's output
            completed_after_cancel = load_completed_cnrs(successes_dir)
            retry_lines = [l for l in chunk if json.loads(l)["custom_id"] not in completed_after_cancel]
            if retry_lines:
                logger.info("  Retrying %d cancelled/missed requests in sub-batches of 50", len(retry_lines))
                for sub_offset in range(0, len(retry_lines), 50):
                    sub = retry_lines[sub_offset: sub_offset + 50]
                    try:
                        retry_id = submit_batch(sub, client)
                        logger.info("    Retry sub-batch submitted: %s (%d requests)", retry_id, len(sub))
                        retry_status = wait_for_completion(retry_id, client)
                        rs, rf = process_batch_results(retry_id, client, output_dir, dedup_map)
                        total_succeeded += rs
                        total_failed += rf
                        logger.info("    Retry processed: %d succeeded  %d failed", rs, rf)
                    except Exception as exc:
                        logger.error("    Retry sub-batch failed: %s", exc)

    elapsed_h = (time.time() - start_time) / 3600
    logger.info(
        "Done — total_succeeded=%d  total_failed=%d  elapsed=%.1fh",
        total_succeeded, total_failed, elapsed_h,
    )


if __name__ == "__main__":
    main()
