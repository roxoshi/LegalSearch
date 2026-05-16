"""Submit the batch JSONL to OpenAI Batch API and save the batch ID.

OpenAI Batch API limits uploads to 100MB per file. This script automatically
splits the JSONL into chunks and submits each as a separate batch job.

Usage:
    OPENAI_API_KEY=sk-... uv run python -m pipelines.submit_openai_batch
    OPENAI_API_KEY=sk-... uv run python -m pipelines.submit_openai_batch --status batch_abc123
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("submit_openai_batch")

CHUNK_SIZE_BYTES = 95 * 1_048_576  # 95MB — safely under 100MB limit
BATCH_IDS_FILE = Path(".data/batch_analysis/openai_batch_ids.txt")


def split_jsonl(jsonl_path: Path) -> list[Path]:
    """Split a large JSONL into ≤95MB chunks. Returns list of chunk paths."""
    chunks_dir = jsonl_path.parent / "batch_chunks"
    chunks_dir.mkdir(exist_ok=True)

    chunk_paths = []
    chunk_idx = 0
    current_size = 0
    current_lines: list[bytes] = []

    def flush(lines: list[bytes], idx: int) -> Path:
        p = chunks_dir / f"chunk_{idx:03d}.jsonl"
        p.write_bytes(b"\n".join(lines) + b"\n")
        logger.info("Chunk %03d: %d requests  %.1f MB", idx, len(lines), p.stat().st_size / 1_048_576)
        return p

    with jsonl_path.open("rb") as f:
        for raw_line in f:
            line = raw_line.rstrip(b"\n")
            if not line:
                continue
            if current_size + len(line) > CHUNK_SIZE_BYTES and current_lines:
                chunk_paths.append(flush(current_lines, chunk_idx))
                chunk_idx += 1
                current_lines = []
                current_size = 0
            current_lines.append(line)
            current_size += len(line) + 1

    if current_lines:
        chunk_paths.append(flush(current_lines, chunk_idx))

    logger.info("Split into %d chunks", len(chunk_paths))
    return chunk_paths


def upload_and_submit(jsonl_path: Path, client) -> str:
    """Upload one JSONL chunk and create a batch job. Returns batch ID."""
    logger.info("Uploading %s (%.1f MB)...", jsonl_path.name, jsonl_path.stat().st_size / 1_048_576)
    with jsonl_path.open("rb") as f:
        file_obj = client.files.create(file=f, purpose="batch")
    logger.info("  File uploaded: %s", file_obj.id)

    batch = client.batches.create(
        input_file_id=file_obj.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    logger.info("  Batch created: %s  status=%s", batch.id, batch.status)
    return batch.id


def submit(jsonl_path: Path) -> list[str]:
    from openai import OpenAI
    client = OpenAI()

    file_size = jsonl_path.stat().st_size
    if file_size <= CHUNK_SIZE_BYTES:
        chunks = [jsonl_path]
    else:
        logger.info("File is %.1f MB — splitting into chunks...", file_size / 1_048_576)
        chunks = split_jsonl(jsonl_path)

    batch_ids = []
    for chunk in chunks:
        bid = upload_and_submit(chunk, client)
        batch_ids.append(bid)

    BATCH_IDS_FILE.write_text("\n".join(batch_ids) + "\n", encoding="utf-8")
    logger.info("Saved %d batch ID(s) to %s", len(batch_ids), BATCH_IDS_FILE)
    return batch_ids


def check_status(batch_ids: list[str]) -> None:
    from openai import OpenAI
    client = OpenAI()
    for batch_id in batch_ids:
        batch = client.batches.retrieve(batch_id)
        rc = batch.request_counts
        logger.info(
            "Batch %s  status=%s  total=%s  completed=%s  failed=%s",
            batch.id, batch.status,
            rc.total if rc else "?",
            rc.completed if rc else "?",
            rc.failed if rc else "?",
        )
        if batch.output_file_id:
            logger.info("  Output file ready: %s", batch.output_file_id)
        if batch.error_file_id:
            logger.info("  Error file: %s", batch.error_file_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit or check OpenAI Batch job(s)")
    parser.add_argument("--jsonl", default=".data/batch_analysis/openai_batch_requests.jsonl")
    parser.add_argument("--status", metavar="BATCH_ID", nargs="+", help="Check status of batch ID(s)")
    parser.add_argument("--status-all", action="store_true", help="Check all saved batch IDs")
    args = parser.parse_args()

    if args.status_all:
        if not BATCH_IDS_FILE.exists():
            logger.error("No saved batch IDs at %s", BATCH_IDS_FILE)
            sys.exit(1)
        ids = [l.strip() for l in BATCH_IDS_FILE.read_text().splitlines() if l.strip()]
        check_status(ids)
    elif args.status:
        check_status(args.status)
    else:
        jsonl_path = Path(args.jsonl)
        if not jsonl_path.exists():
            logger.error("JSONL not found: %s — run build_openai_batch.py first", jsonl_path)
            sys.exit(1)
        batch_ids = submit(jsonl_path)
        print(f"\nSubmitted {len(batch_ids)} batch job(s):")
        for bid in batch_ids:
            print(f"  {bid}")
        print(f"\nCheck status: uv run python -m pipelines.submit_openai_batch --status-all")


if __name__ == "__main__":
    main()
