"""Gemini Batch API pipeline for bulk case law analysis.

Submits all PDFs as a single asynchronous batch job to the Gemini Batch API,
which processes them at 50% of standard API cost with a ~24-hour turnaround.

This module mirrors the structure of llm_analyze.py but replaces synchronous
per-PDF calls with a single batch submission. All parsing and validation logic
is shared from llm_analyze.

Output layout:
    {output_dir}/
        successes/{stem}.json          — validated analysis JSON
        failures/{stem}_{error}.log    — error details + raw LLM response
        batch_requests.jsonl           — uploaded JSONL (kept for auditing/resume)
        batch_results.jsonl            — raw downloaded results
        job_name.txt                   — Gemini job name (use with --resume)

Usage (API):
    from pathlib import Path
    from pipelines.gemini_batch_analyze import run_batch_analysis, discover_pdfs

    stats = run_batch_analysis(
        pdf_paths=discover_pdfs(Path("/data/cases")),
        output_dir=Path(".data/batch_analysis"),
        model="gemini-2.0-flash",
    )

Usage (CLI — AI Studio API key, simplest):
    export GOOGLE_API_KEY="your-ai-studio-key"
    python -m pipelines.gemini_batch_analyze \\
        --input /data/cases/ \\
        --output .data/batch_analysis

Usage (CLI — Vertex AI credentials):
    gcloud auth application-default login   # one-time setup
    python -m pipelines.gemini_batch_analyze \\
        --input /data/cases/ \\
        --vertex --project my-gcp-project --location us-central1 \\
        --output .data/batch_analysis

Usage (CLI — resume polling an existing job):
    python -m pipelines.gemini_batch_analyze \\
        --resume batches/abc123 \\
        --input /data/cases/ \\
        --output .data/batch_analysis

    # Without --input (uses placeholder paths — JSON output still named correctly):
    python -m pipelines.gemini_batch_analyze \\
        --resume batches/abc123 \\
        --output .data/batch_analysis

Authentication modes:
    AI Studio (default):
        Obtain a key at https://aistudio.google.com/app/apikey
        Set GOOGLE_API_KEY env var. Works with a simple API key.

    Vertex AI (--vertex flag):
        Uses Application Default Credentials (ADC) — no API key needed.
        Set up once with: gcloud auth application-default login
        Or set GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
        Requires --project (GCP project ID) and --location (e.g. us-central1).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

# Reuse schema, helpers, and result types from llm_analyze
from .llm_analyze import (
    AnalysisFailure,
    AnalysisSuccess,
    CaseLawAnalysis,
    ERROR_API,
    ERROR_EXTRACTION,
    ERROR_PARSE,
    ERROR_VALIDATION,
    RunStats,
    discover_pdfs,
    extract_text,
    parse_json_response,
    write_failure,
    write_success,
)
from .llm_providers import load_prompt

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-flash-preview"
POLL_INTERVAL = 180  # seconds between status checks

_COMPLETED_STATES = frozenset({
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
})


# ── Client factory ─────────────────────────────────────────────────────────────


def _get_client(
    api_key: str | None = None,
    vertexai: bool = False,
    project: str | None = None,
    location: str | None = None,
):
    """Instantiate a google-genai Client.

    Two authentication modes are supported:

    **AI Studio** (default, ``vertexai=False``):
        Authenticates with a simple API key.
        Obtain one at https://aistudio.google.com/app/apikey
        Pass via ``api_key`` or the ``GOOGLE_API_KEY`` environment variable.

    **Vertex AI** (``vertexai=True``):
        Authenticates using Application Default Credentials (ADC) — no API key.
        Set up once with ``gcloud auth application-default login``, or point
        ``GOOGLE_APPLICATION_CREDENTIALS`` at a service-account JSON key file.
        ``project`` and ``location`` are required (or set ``GOOGLE_CLOUD_PROJECT``
        and ``GOOGLE_CLOUD_LOCATION`` env vars).

    Raises:
        ImportError:  If ``google-genai`` is not installed.
        ValueError:   If Vertex AI mode is requested but project/location are missing.
    """
    try:
        from google import genai  # google-genai package
    except ImportError as exc:
        raise ImportError(
            "google-genai package is required: pip install 'pipelines[gemini-batch]'"
        ) from exc

    if vertexai:
        resolved_project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        resolved_location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        if not resolved_project:
            raise ValueError(
                "Vertex AI mode requires a GCP project. "
                "Pass --project or set GOOGLE_CLOUD_PROJECT."
            )
        logger.info(
            "Using Vertex AI backend (project=%s, location=%s). "
            "Auth via Application Default Credentials.",
            resolved_project,
            resolved_location,
        )
        return genai.Client(
            vertexai=True,
            project=resolved_project,
            location=resolved_location,
        )

    return genai.Client(api_key=api_key or os.environ["GOOGLE_API_KEY"])


# ── Request building ───────────────────────────────────────────────────────────


def build_batch_requests(
    pdf_paths: list[Path],
    max_output_tokens: int = 4096,
) -> tuple[list[dict], dict[str, AnalysisFailure]]:
    """Extract text from each PDF and produce a JSONL request dict per PDF.

    The PROMPT.md content goes into ``system_instruction`` and the extracted
    case text goes into ``contents``, mirroring a clean Chat-style prompt.
    ``response_mime_type: application/json`` instructs Gemini to output JSON
    directly, reducing the chance of markdown-fence wrapping.

    Args:
        pdf_paths:        PDFs to process.
        max_output_tokens: Token budget for each individual response.

    Returns:
        A tuple of:
          - requests: list of dicts ready for JSONL serialisation, one per
            successfully extracted PDF. The ``key`` is the PDF stem so results
            can be mapped back to source files.
          - extraction_failures: mapping of ``stem → AnalysisFailure`` for PDFs
            that could not be read. These are written to the failures/ directory
            before the batch job is even submitted.
    """
    prompt = load_prompt()
    requests: list[dict] = []
    extraction_failures: dict[str, AnalysisFailure] = {}

    for pdf_path in pdf_paths:
        try:
            text = extract_text(pdf_path)
        except Exception as exc:
            stem = pdf_path.stem
            extraction_failures[stem] = AnalysisFailure(
                pdf_path=pdf_path,
                error_type=ERROR_EXTRACTION,
                error_detail=str(exc),
            )
            logger.warning("Extraction failed for %s: %s", pdf_path.name, exc)
            continue

        requests.append({
            "key": pdf_path.stem,
            "request": {
                "system_instruction": {
                    "parts": [{"text": prompt}],
                },
                "contents": [
                    {"role": "user", "parts": [{"text": text}]},
                ],
                "generation_config": {
                    "max_output_tokens": max_output_tokens,
                    "response_mime_type": "application/json",
                },
            },
        })

    return requests, extraction_failures


def write_jsonl(requests: list[dict], path: Path) -> None:
    """Serialise a list of dicts to a JSONL file (one JSON object per line)."""
    with path.open("w", encoding="utf-8") as fh:
        for req in requests:
            fh.write(json.dumps(req, ensure_ascii=False) + "\n")


# ── GCS helpers (Vertex AI mode only) ─────────────────────────────────────────


def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """Split ``gs://bucket/path`` into ``(bucket_name, blob_path)``."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Expected a gs:// URI, got: {gcs_uri!r}")
    remainder = gcs_uri[5:]
    bucket, _, blob = remainder.partition("/")
    return bucket, blob


def _gcs_upload(local_path: Path, gcs_uri: str, project: str) -> str:
    """Upload *local_path* to *gcs_uri* (``gs://bucket/blob``). Returns the URI."""
    try:
        from google.cloud import storage as gcs_storage
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage is required for Vertex AI mode: "
            "pip install 'pipelines[gemini-batch]'"
        ) from exc

    bucket_name, blob_name = _parse_gcs_uri(gcs_uri)
    client = gcs_storage.Client(project=project)
    blob = client.bucket(bucket_name).blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info("Uploaded to GCS: %s", gcs_uri)
    return gcs_uri


def _gcs_download_prefix(gcs_prefix: str, project: str) -> str:
    """Download all blobs under *gcs_prefix* and return concatenated text.

    Vertex AI batch jobs write one or more JSONL shards into a GCS prefix.
    This function collects them all into a single JSONL string.
    """
    try:
        from google.cloud import storage as gcs_storage
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage is required for Vertex AI mode: "
            "pip install 'pipelines[gemini-batch]'"
        ) from exc

    bucket_name, prefix = _parse_gcs_uri(gcs_prefix)
    gcs_client = gcs_storage.Client(project=project)
    bucket = gcs_client.bucket(bucket_name)

    blobs = sorted(
        bucket.list_blobs(prefix=prefix),
        key=lambda b: b.name,
    )
    if not blobs:
        raise ValueError(f"No files found at GCS prefix: {gcs_prefix}")

    logger.info("Downloading %d result shard(s) from %s", len(blobs), gcs_prefix)
    return "\n".join(blob.download_as_text() for blob in blobs)


# ── Job submission ─────────────────────────────────────────────────────────────


def submit_batch_job(
    client,
    jsonl_path: Path,
    model: str,
    display_name: str,
    gcs_input_uri: str | None = None,
    gcs_output_uri: str | None = None,
    gcs_project: str | None = None,
) -> object:
    """Upload the JSONL file and create a Batch job.

    **AI Studio mode** (``gcs_input_uri`` is None):
        Uploads via the Files API (``client.files.upload``), passes the file
        name as ``src``. Results land in the Files API (``job.dest.file_name``).

    **Vertex AI mode** (``gcs_input_uri`` provided):
        Uploads the JSONL to ``gcs_input_uri`` via ``google-cloud-storage``,
        passes a ``gs://`` URI as ``src``. Results are written to
        ``gcs_output_uri`` (a GCS prefix you own).

    Args:
        client:          google-genai Client instance.
        jsonl_path:      Local path to the JSONL request file.
        model:           Gemini model name.
        display_name:    Human-readable label for the job.
        gcs_input_uri:   Vertex AI only — ``gs://bucket/path/requests.jsonl``.
        gcs_output_uri:  Vertex AI only — ``gs://bucket/path/results/`` prefix.

    Returns:
        The newly created batch job object (has ``.name`` and ``.state``).
    """
    if gcs_input_uri:
        # ── Vertex AI path ────────────────────────────────────────────────────
        _gcs_upload(jsonl_path, gcs_input_uri, project=gcs_project)
        job = client.batches.create(
            model=model,
            src=gcs_input_uri,
            config={"display_name": display_name, "dest": gcs_output_uri},
        )
    else:
        # ── AI Studio path ────────────────────────────────────────────────────
        from google.genai import types

        logger.info("Uploading request file %s via Files API …", jsonl_path.name)
        uploaded = client.files.upload(
            file=str(jsonl_path),
            config=types.UploadFileConfig(
                display_name=display_name,
                mime_type="jsonl",
            ),
        )
        logger.info("File uploaded: %s", uploaded.name)
        job = client.batches.create(
            model=model,
            src=uploaded.name,
            config={"display_name": display_name},
        )

    logger.info("Batch job created: %s  (state: %s)", job.name, job.state.name)
    return job


# ── Polling ────────────────────────────────────────────────────────────────────


def poll_until_done(
    client,
    job_name: str,
    poll_interval: int = POLL_INTERVAL,
) -> object:
    """Block until the batch job reaches a terminal state, then return it.

    Polls ``client.batches.get`` every ``poll_interval`` seconds.
    Terminal states: SUCCEEDED, FAILED, CANCELLED, EXPIRED.
    """
    job = client.batches.get(name=job_name)
    while job.state.name not in _COMPLETED_STATES:
        logger.info(
            "Job %s — state: %s. Checking again in %ds …",
            job_name,
            job.state.name,
            poll_interval,
        )
        time.sleep(poll_interval)
        job = client.batches.get(name=job_name)

    logger.info("Job %s finished with state: %s", job_name, job.state.name)
    return job


# ── Result download ────────────────────────────────────────────────────────────


def download_results(client, job, gcs_output_uri: str | None = None, gcs_project: str | None = None) -> str:
    """Download the result JSONL from the completed job and return as a string.

    **AI Studio** (``gcs_output_uri`` is None):
        Downloads via ``client.files.download`` using ``job.dest.file_name``.

    **Vertex AI** (``gcs_output_uri`` provided):
        Downloads all shards from the GCS output prefix and concatenates them.

    The JSONL content has one record per line:
      - ``{"key": "...", "response": {GenerateContentResponse}}``  — success
      - ``{"key": "...", "error": {"code": N, "message": "..."}}`` — failure
    """
    if gcs_output_uri:
        return _gcs_download_prefix(gcs_output_uri, project=gcs_project)

    result_file_name = job.dest.file_name
    logger.info("Downloading results from Files API: %s", result_file_name)
    raw_bytes: bytes = client.files.download(file=result_file_name)
    return raw_bytes.decode("utf-8")


# ── Result parsing ─────────────────────────────────────────────────────────────


def _text_from_response(response_dict: dict) -> str | None:
    """Extract the first text part from a raw Gemini API response dict.

    Navigates: response → candidates[0] → content → parts[0] → text
    Returns None if the path does not exist.
    """
    try:
        return response_dict["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


def process_results(
    result_content: str,
    pdf_by_stem: dict[str, Path],
    success_dir: Path,
    failure_dir: Path,
) -> RunStats:
    """Parse each JSONL result line, validate against CaseLawAnalysis, write outputs.

    For every PDF stem that appears in ``pdf_by_stem`` but is absent from the
    result file, an ``api_error`` failure is written (the batch job produced no
    output for that request).

    Args:
        result_content: Raw JSONL string downloaded from the batch result file.
        pdf_by_stem:    Mapping of PDF stem → original Path for metadata.
        success_dir:    Directory for ``{stem}.json`` success files.
        failure_dir:    Directory for ``{stem}_{error_type}.log`` failure files.

    Returns:
        RunStats with counts broken down by outcome (total not set here).
    """
    stats = RunStats()
    processed_stems: set[str] = set()

    for line_no, line in enumerate(result_content.splitlines(), 1):
        line = line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("Line %d: could not parse JSONL — %s", line_no, exc)
            continue

        key: str = record.get("key", f"unknown_line_{line_no}")
        # Use the real Path if known; otherwise create a placeholder so writers work
        pdf_path = pdf_by_stem.get(key, Path(f"{key}.pdf"))
        processed_stems.add(key)

        # ── API-level error ────────────────────────────────────────────────────
        # AI Studio returns {"error": {...}}.
        # Vertex AI returns {"status": "{\"code\":N,\"message\":\"...\"}"}
        # with an empty or absent "response" dict when the request failed.
        api_error_detail: str | None = None
        if "error" in record:
            api_error_detail = json.dumps(record["error"])
        elif "status" in record:
            raw_status = record["status"]
            try:
                status = json.loads(raw_status) if isinstance(raw_status, str) else raw_status
            except (json.JSONDecodeError, TypeError):
                status = {"message": str(raw_status)}
            # code 0 (or absent) means OK; anything else is an error
            if status.get("code", 0) != 0:
                api_error_detail = status.get("message", raw_status)

        if api_error_detail is not None:
            failure = AnalysisFailure(
                pdf_path=pdf_path,
                error_type=ERROR_API,
                error_detail=api_error_detail,
                raw_response=json.dumps(record),
            )
            write_failure(failure, failure_dir)
            stats.failed_api += 1
            logger.warning("  ✗ [api_error] %s: %s", key, api_error_detail)
            continue

        # ── Extract text from response ─────────────────────────────────────────
        raw_response = _text_from_response(record.get("response", {}))
        if raw_response is None:
            failure = AnalysisFailure(
                pdf_path=pdf_path,
                error_type=ERROR_PARSE,
                error_detail="Response contained no text in candidates[0].content.parts[0].text",
                raw_response=json.dumps(record),
            )
            write_failure(failure, failure_dir)
            stats.failed_parse += 1
            logger.warning("  ✗ [parse_error] %s: empty/missing response text", key)
            continue

        # ── Parse JSON ────────────────────────────────────────────────────────
        try:
            data = parse_json_response(raw_response)
        except json.JSONDecodeError as exc:
            failure = AnalysisFailure(
                pdf_path=pdf_path,
                error_type=ERROR_PARSE,
                error_detail=str(exc),
                raw_response=raw_response,
            )
            write_failure(failure, failure_dir)
            stats.failed_parse += 1
            logger.warning("  ✗ [parse_error] %s: %s", key, exc)
            continue

        # Some models wrap the JSON object in a single-element array — unwrap it.
        if isinstance(data, list):
            if len(data) == 1 and isinstance(data[0], dict):
                data = data[0]
            else:
                failure = AnalysisFailure(
                    pdf_path=pdf_path,
                    error_type=ERROR_PARSE,
                    error_detail=f"Expected a JSON object, got a list with {len(data)} item(s)",
                    raw_response=raw_response,
                )
                write_failure(failure, failure_dir)
                stats.failed_parse += 1
                logger.warning("  ✗ [parse_error] %s: response is a JSON array, not an object", key)
                continue

        # ── Validate Pydantic schema ───────────────────────────────────────────
        try:
            analysis = CaseLawAnalysis(**data)
        except ValidationError as exc:
            failure = AnalysisFailure(
                pdf_path=pdf_path,
                error_type=ERROR_VALIDATION,
                error_detail=exc.json(),
                raw_response=raw_response,
            )
            write_failure(failure, failure_dir)
            stats.failed_validation += 1
            logger.warning("  ✗ [validation_error] %s", key)
            continue

        # ── Write success ──────────────────────────────────────────────────────
        result = AnalysisSuccess(
            pdf_path=pdf_path,
            analysis=analysis,
            raw_response=raw_response,
        )
        out = write_success(result, success_dir)
        stats.succeeded += 1
        logger.info("  ✓ %s", out.name)

    # Any expected stem with no result line → implicit API failure
    for stem, pdf_path in pdf_by_stem.items():
        if stem not in processed_stems:
            failure = AnalysisFailure(
                pdf_path=pdf_path,
                error_type=ERROR_API,
                error_detail="No result line found for this key in batch output",
            )
            write_failure(failure, failure_dir)
            stats.failed_api += 1
            logger.warning("  ✗ [api_error] %s: missing from batch results", stem)

    return stats


# ── Pipeline entry points ──────────────────────────────────────────────────────


def run_batch_analysis(
    pdf_paths: list[Path],
    output_dir: Path,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    max_output_tokens: int = 4096,
    poll_interval: int = POLL_INTERVAL,
    display_name: str | None = None,
    vertexai: bool = False,
    project: str | None = None,
    location: str | None = None,
    gcs_bucket: str | None = None,
) -> RunStats:
    """Full Gemini Batch pipeline: extract → upload → submit → poll → parse → write.

    Args:
        pdf_paths:         PDFs to analyse. Use :func:`discover_pdfs` to build this list.
        output_dir:        Root output directory. Sub-directories are created automatically.
        model:             Gemini model name (e.g. ``"gemini-3-flash-preview"``).
        api_key:           AI Studio API key. Falls back to ``GOOGLE_API_KEY`` env var.
                           Ignored when ``vertexai=True``.
        max_output_tokens: Token budget per individual model response.
        poll_interval:     Seconds between batch job status checks.
        display_name:      Label shown in the console. Auto-generated if omitted.
        vertexai:          Use Vertex AI backend instead of AI Studio. Authenticates
                           via Application Default Credentials (no API key needed).
        project:           GCP project ID (Vertex AI mode only). Falls back to
                           ``GOOGLE_CLOUD_PROJECT`` env var.
        location:          GCP region (Vertex AI mode only, e.g. ``"us-central1"``).
                           Falls back to ``GOOGLE_CLOUD_LOCATION`` env var.
        gcs_bucket:        GCS bucket name (required for Vertex AI mode, e.g.
                           ``"my-bucket"``). Input JSONL and output results are
                           stored here under a ``legalsearch-batch/{display_name}/``
                           prefix. Not used in AI Studio mode.

    Returns:
        :class:`RunStats` with outcome counts broken down by category.
    """
    if vertexai and not gcs_bucket:
        raise ValueError(
            "gcs_bucket is required for Vertex AI mode. "
            "Pass --gcs-bucket my-bucket-name or set gcs_bucket='my-bucket'."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    success_dir = output_dir / "successes"
    failure_dir = output_dir / "failures"
    success_dir.mkdir(exist_ok=True)
    failure_dir.mkdir(exist_ok=True)

    resolved_project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    client = _get_client(api_key, vertexai=vertexai, project=resolved_project, location=location)
    pdf_by_stem = {p.stem: p for p in pdf_paths}

    # ── Step 1: extract text and build JSONL ──────────────────────────────────
    logger.info("Extracting text from %d PDF(s) …", len(pdf_paths))
    requests, extraction_failures = build_batch_requests(pdf_paths, max_output_tokens)

    for failure in extraction_failures.values():
        write_failure(failure, failure_dir)

    logger.info(
        "Built %d request(s). Extraction failures: %d",
        len(requests),
        len(extraction_failures),
    )

    if not requests:
        logger.error("No valid PDFs to process — aborting.")
        stats = RunStats(total=len(pdf_paths))
        stats.failed_extraction = len(extraction_failures)
        return stats

    # ── Step 2: write JSONL and submit ────────────────────────────────────────
    name = display_name or f"legalsearch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    jsonl_path = output_dir / "batch_requests.jsonl"
    write_jsonl(requests, jsonl_path)
    logger.info("Request file saved: %s", jsonl_path)

    # Build GCS URIs for Vertex AI mode
    gcs_input_uri: str | None = None
    gcs_output_uri: str | None = None
    if vertexai and gcs_bucket:
        base = f"gs://{gcs_bucket}/legalsearch-batch/{name}"
        gcs_input_uri = f"{base}/requests.jsonl"
        gcs_output_uri = f"{base}/results/"
        logger.info("GCS input:  %s", gcs_input_uri)
        logger.info("GCS output: %s", gcs_output_uri)

    job = submit_batch_job(
        client,
        jsonl_path,
        model=model,
        display_name=name,
        gcs_input_uri=gcs_input_uri,
        gcs_output_uri=gcs_output_uri,
        gcs_project=resolved_project,
    )

    # Save job name so the user can resume if the process is interrupted
    job_name_file = output_dir / "job_name.txt"
    job_name_file.write_text(job.name, encoding="utf-8")
    logger.info(
        "Job name saved to %s — use --resume %s to resume if interrupted",
        job_name_file,
        job.name,
    )

    # ── Step 3: poll ──────────────────────────────────────────────────────────
    job = poll_until_done(client, job.name, poll_interval)

    if job.state.name != "JOB_STATE_SUCCEEDED":
        logger.error(
            "Batch job ended with state %s. No results to process.", job.state.name
        )
        stats = RunStats(total=len(pdf_paths))
        stats.failed_extraction = len(extraction_failures)
        stats.failed_api = len(requests)
        return stats

    # ── Step 4: download results ───────────────────────────────────────────────
    result_content = download_results(client, job, gcs_output_uri=gcs_output_uri, gcs_project=resolved_project)
    results_path = output_dir / "batch_results.jsonl"
    results_path.write_text(result_content, encoding="utf-8")
    logger.info("Results saved to %s", results_path)

    # ── Step 5: parse and write outputs ───────────────────────────────────────
    logger.info("Processing results …")
    stats = process_results(result_content, pdf_by_stem, success_dir, failure_dir)
    stats.failed_extraction = len(extraction_failures)
    stats.total = len(pdf_paths)

    logger.info(
        "Done — %d/%d succeeded | %d failed "
        "(extract:%d  api:%d  parse:%d  valid:%d)",
        stats.succeeded,
        stats.total,
        stats.failed,
        stats.failed_extraction,
        stats.failed_api,
        stats.failed_parse,
        stats.failed_validation,
    )
    return stats


def resume_batch_analysis(
    job_name: str,
    output_dir: Path,
    pdf_paths: list[Path] | None = None,
    api_key: str | None = None,
    poll_interval: int = POLL_INTERVAL,
    vertexai: bool = False,
    project: str | None = None,
    location: str | None = None,
    gcs_output_uri: str | None = None,
) -> RunStats:
    """Resume polling an existing batch job, then download and process results.

    Use this when the original process was interrupted after job submission.
    The job name is printed to stdout and written to ``{output_dir}/job_name.txt``
    during a normal run.

    Args:
        job_name:       Gemini job name, e.g. ``"batches/abc123"``.
        output_dir:     Same directory used during the original run. Results are
                        appended here (successes/, failures/, batch_results.jsonl).
        pdf_paths:      Optional — original PDF list for stem→Path mapping in failure
                        logs. If omitted, placeholder paths are used (filenames still
                        correct).
        api_key:        AI Studio API key. Ignored when ``vertexai=True``.
        poll_interval:  Seconds between job status checks.
        vertexai:       Use Vertex AI backend (ADC auth).
        project:        GCP project ID (Vertex AI only).
        location:       GCP region (Vertex AI only).
        gcs_output_uri: Vertex AI only — the GCS output prefix used when the job
                        was originally submitted (printed in the original run's logs
                        as "GCS output: gs://..."). Required to download results in
                        Vertex AI resume mode.

    Returns:
        :class:`RunStats` with outcome counts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    success_dir = output_dir / "successes"
    failure_dir = output_dir / "failures"
    success_dir.mkdir(exist_ok=True)
    failure_dir.mkdir(exist_ok=True)

    resolved_project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    client = _get_client(api_key, vertexai=vertexai, project=resolved_project, location=location)
    pdf_by_stem: dict[str, Path] = {p.stem: p for p in (pdf_paths or [])}

    logger.info("Resuming job: %s", job_name)
    job = poll_until_done(client, job_name, poll_interval)

    if job.state.name != "JOB_STATE_SUCCEEDED":
        logger.error("Job ended with state %s", job.state.name)
        total = len(pdf_by_stem)
        return RunStats(total=total, failed_api=total)

    result_content = download_results(client, job, gcs_output_uri=gcs_output_uri, gcs_project=resolved_project)
    results_path = output_dir / "batch_results.jsonl"
    results_path.write_text(result_content, encoding="utf-8")
    logger.info("Results saved to %s", results_path)

    stats = process_results(result_content, pdf_by_stem, success_dir, failure_dir)
    stats.total = len(pdf_by_stem) if pdf_by_stem else stats.succeeded + stats.failed

    logger.info(
        "Done — %d/%d succeeded | %d failed "
        "(extract:%d  api:%d  parse:%d  valid:%d)",
        stats.succeeded,
        stats.total,
        stats.failed,
        stats.failed_extraction,
        stats.failed_api,
        stats.failed_parse,
        stats.failed_validation,
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────────


def _cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="python -m pipelines.gemini_batch_analyze",
        description="Bulk-analyze legal judgment PDFs via the Gemini Batch API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Authentication:
  AI Studio (default) — get a key at https://aistudio.google.com/app/apikey
    export GOOGLE_API_KEY="your-key"

  Vertex AI — uses Application Default Credentials, no API key needed.
  Also requires a GCS bucket for input/output transfer.
    gcloud auth application-default login
    # then pass --vertex --project ... --location ... --gcs-bucket ...

Examples:
  # AI Studio key (default):
  python -m pipelines.gemini_batch_analyze --input /data/cases/ --output .data/batch

  # Vertex AI credentials (needs a GCS bucket you own):
  python -m pipelines.gemini_batch_analyze --input /data/cases/ --output .data/batch \\
      --vertex --project my-gcp-project --location us-central1 \\
      --gcs-bucket my-gcs-bucket

  # Resume a Vertex AI job that was interrupted (note --gcs-output-uri):
  python -m pipelines.gemini_batch_analyze --output .data/batch \\
      --resume batches/abc123 --input /data/cases/ \\
      --vertex --project my-gcp-project --location us-central1 \\
      --gcs-output-uri gs://my-gcs-bucket/legalsearch-batch/legalsearch-20250301T120000/results/

  # Specify a different model:
  python -m pipelines.gemini_batch_analyze --input /data/cases/ \\
      --model gemini-2.0-flash-lite --output .data/batch

  # Resume an AI Studio job (no GCS needed):
  python -m pipelines.gemini_batch_analyze \\
      --resume batches/abc123 --input /data/cases/ --output .data/batch
""",
    )

    parser.add_argument(
        "--input",
        nargs="+",
        metavar="PATH",
        help="One or more PDF files or a directory of PDFs. "
             "Required for a new job; optional (but recommended) when --resume is used.",
    )
    parser.add_argument(
        "--resume",
        metavar="JOB_NAME",
        default=None,
        help="Skip submission and resume polling an existing job "
             "(e.g. 'batches/abc123'). The job name is printed on submission "
             "and written to {output_dir}/job_name.txt.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output",
        default=".data/gemini_batch",
        metavar="DIR",
        help="Output directory (default: .data/gemini_batch)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Max output tokens per response (default: 4096)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=POLL_INTERVAL,
        metavar="SECONDS",
        help=f"Seconds between job status checks (default: {POLL_INTERVAL})",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Human-readable job label in the Google AI Studio console "
             "(auto-generated from UTC timestamp if omitted)",
    )

    # ── Auth flags ──────────────────────────────────────────────────────────────
    auth_group = parser.add_argument_group(
        "authentication",
        "Choose between AI Studio API key (default) and Vertex AI credentials.",
    )
    auth_group.add_argument(
        "--vertex",
        action="store_true",
        default=False,
        help="Use Vertex AI backend with Application Default Credentials "
             "(gcloud auth application-default login). "
             "Requires --project; ignores GOOGLE_API_KEY.",
    )
    auth_group.add_argument(
        "--project",
        default=None,
        metavar="GCP_PROJECT",
        help="GCP project ID (Vertex AI mode only). "
             "Falls back to GOOGLE_CLOUD_PROJECT env var.",
    )
    auth_group.add_argument(
        "--location",
        default=None,
        metavar="REGION",
        help="GCP region for Vertex AI (default: us-central1). "
             "Falls back to GOOGLE_CLOUD_LOCATION env var.",
    )
    auth_group.add_argument(
        "--gcs-bucket",
        default=None,
        metavar="BUCKET",
        help="GCS bucket name for Vertex AI mode (required with --vertex). "
             "Input JSONL and output results are stored under "
             "gs://BUCKET/legalsearch-batch/{display_name}/. "
             "The bucket must already exist and be writable by your credentials.",
    )
    auth_group.add_argument(
        "--gcs-output-uri",
        default=None,
        metavar="GCS_URI",
        help="Vertex AI resume mode only — the gs://bucket/path/results/ URI "
             "printed during the original run ('GCS output: gs://...'). "
             "Required when --resume is used with --vertex.",
    )

    args = parser.parse_args()

    if not args.resume and not args.input:
        parser.error("--input is required when not using --resume")

    output_dir = Path(args.output)

    # Resolve PDF paths (used for both new jobs and stem→path mapping in resume)
    pdf_paths: list[Path] = []
    if args.input:
        for raw in args.input:
            pdf_paths.extend(discover_pdfs(Path(raw)))
        logger.info("Found %d PDF(s)", len(pdf_paths))

    if args.resume:
        stats = resume_batch_analysis(
            job_name=args.resume,
            output_dir=output_dir,
            pdf_paths=pdf_paths or None,
            poll_interval=args.poll_interval,
            vertexai=args.vertex,
            project=args.project,
            location=args.location,
            gcs_output_uri=args.gcs_output_uri,
        )
    else:
        stats = run_batch_analysis(
            pdf_paths=pdf_paths,
            output_dir=output_dir,
            model=args.model,
            max_output_tokens=args.max_tokens,
            poll_interval=args.poll_interval,
            display_name=args.display_name,
            vertexai=args.vertex,
            project=args.project,
            location=args.location,
            gcs_bucket=args.gcs_bucket,
        )

    if stats.failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    _cli()
