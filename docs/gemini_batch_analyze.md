# Gemini Batch Analyze — User Guide

`pipelines/gemini_batch_analyze.py` bulk-analyses legal judgment PDFs using the
[Gemini Batch API](https://ai.google.dev/gemini-api/docs/batch-api). Instead of
one API call per PDF (like `llm_analyze.py`), it bundles all requests into a
single asynchronous job — **50% cheaper** than standard API pricing with a ~24-hour
turnaround.

---

## When to use this vs. `llm_analyze.py`

| Situation | Use |
|---|---|
| Fewer than ~50 PDFs, need results now | `llm_analyze.py` (synchronous) |
| Hundreds or thousands of PDFs | **this module** (batch) |
| Cost is a priority over speed | **this module** |
| Interactive / iterative testing | `llm_analyze.py` |

---

## Setup

### 1. Install the dependency

The batch module uses the **`google-genai`** SDK (distinct from the
`google-generativeai` package used by the synchronous provider):

```bash
# From the pipelines/ directory
uv sync --extra gemini-batch

# Or with plain pip
pip install "google-genai>=1.0.0" "pymupdf>=1.26.7"
```

### 2. Authenticate

There are two backends. Use whichever matches your Google account setup:

#### Option A — Google AI Studio API key (simplest)

Get a key at [Google AI Studio → API Keys](https://aistudio.google.com/app/apikey).
This is a free personal key separate from Google Cloud / Vertex AI.

```bash
export GOOGLE_API_KEY="your-ai-studio-key"
```

#### Option B — Vertex AI (Google Cloud project)

If you have a GCP project, use Application Default Credentials instead of an
API key. No `GOOGLE_API_KEY` needed.

The Vertex AI backend cannot use the Files API for transfer, so it uses a
**GCS bucket** you own as the input/output medium.

**Prerequisites:**
1. A GCP project with Vertex AI API enabled
2. A GCS bucket in the same project (any standard bucket, e.g. `gs://my-legal-batch`)
3. Your credentials have `roles/storage.objectAdmin` on that bucket

```bash
# One-time auth setup (opens a browser):
gcloud auth application-default login

# Run with --vertex, --project, --location, and --gcs-bucket:
python -m pipelines.gemini_batch_analyze \
    --input /data/cases/ \
    --vertex --project my-gcp-project --location us-central1 \
    --gcs-bucket my-legal-batch \
    --output .data/batch_analysis
```

The JSONL is uploaded to `gs://my-legal-batch/legalsearch-batch/{job-name}/requests.jsonl`
and results are downloaded from `gs://my-legal-batch/legalsearch-batch/{job-name}/results/`.

Alternatively, authenticate with a service account key file:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
export GOOGLE_CLOUD_PROJECT="my-gcp-project"
python -m pipelines.gemini_batch_analyze \
    --input /data/cases/ --vertex --gcs-bucket my-legal-batch \
    --output .data/batch_analysis
```

> **Common mistake**: Vertex AI credentials are **not** simple API keys and
> will not work without `--vertex`. If you see a `401 UNAUTHENTICATED` error
> saying "API keys are not supported", you need either a proper AI Studio key
> or the `--vertex --gcs-bucket ...` flags.
>
> The other common error — `ValueError: This method is only supported in the
> Gemini Developer client` — means you passed `--vertex` but forgot `--gcs-bucket`.

---

## Running a batch job

### Full run (most common)

```bash
python -m pipelines.gemini_batch_analyze \
    --input /data/cases/ \
    --output .data/batch_analysis
```

This will:
1. Extract text from every PDF under `/data/cases/`
2. Build a JSONL request file and upload it to Google
3. Submit the batch job and print the job name
4. Poll every 60 seconds until the job finishes (can take minutes to hours)
5. Download results and write them to `.data/batch_analysis/`

### Specify a model

```bash
python -m pipelines.gemini_batch_analyze \
    --input /data/cases/ \
    --model gemini-2.0-flash-lite \     # cheaper/faster
    --output .data/batch_analysis
```

Available models that support the Batch API (check
[Google's model page](https://ai.google.dev/gemini-api/docs/models) for the
current list):

| Model | Notes |
|---|---|
| `gemini-2.0-flash` | Default. Good balance of quality and cost. |
| `gemini-2.0-flash-lite` | Fastest and cheapest. |
| `gemini-1.5-pro` | Highest quality, slower, more expensive. |

### Multiple input paths

```bash
python -m pipelines.gemini_batch_analyze \
    --input /data/cases/2023/ /data/cases/2024/ extra.pdf \
    --output .data/batch_analysis
```

### Custom job name (visible in Google AI Studio console)

```bash
python -m pipelines.gemini_batch_analyze \
    --input /data/cases/ \
    --display-name "GST-HC-2024-batch" \
    --output .data/batch_analysis
```

---

## Resuming an interrupted job

If your process is killed after submission but before results are saved, you can
resume without re-uploading or re-submitting:

```bash
# The job name is printed on submission and saved in {output_dir}/job_name.txt
cat .data/batch_analysis/job_name.txt
# → batches/abc123def456

python -m pipelines.gemini_batch_analyze \
    --resume batches/abc123def456 \
    --input /data/cases/ \          # recommended: for correct stem→path mapping
    --output .data/batch_analysis
```

`--input` is optional in resume mode. Without it, output filenames are still
named correctly (by PDF stem from the JSONL key), but failure log paths show
placeholder filenames instead of the real PDF paths.

---

## CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--input PATH [PATH …]` | — | PDF files or directory. Required unless `--resume` is used. |
| `--resume JOB_NAME` | — | Resume polling an existing job (e.g. `batches/abc123`). |
| `--model MODEL` | `gemini-3-flash-preview` | Gemini model to use. |
| `--output DIR` | `.data/gemini_batch` | Root output directory. |
| `--max-tokens N` | `4096` | Max output tokens per PDF response. |
| `--poll-interval SECONDS` | `180` | How often to check job status. |
| `--display-name NAME` | auto (UTC timestamp) | Label shown in Google AI Studio. |
| `--vertex` | off | Use Vertex AI backend with ADC (no API key). |
| `--project GCP_PROJECT` | `GOOGLE_CLOUD_PROJECT` env | GCP project ID (Vertex AI only). |
| `--location REGION` | `us-central1` | GCP region (Vertex AI only). |
| `--gcs-bucket BUCKET` | — | GCS bucket for JSONL transfer (required with `--vertex`). |
| `--gcs-output-uri GS_URI` | — | Resume only: the `gs://...results/` URI printed during the original run. |

---

## Output layout

```
{output_dir}/
├── successes/
│   ├── case_abc.json          ← validated analysis for each PDF
│   └── case_xyz.json
├── failures/
│   ├── bad_scan_extraction_error.log
│   └── case_foo_validation_error.log
├── batch_requests.jsonl       ← uploaded request file (audit trail)
├── batch_results.jsonl        ← raw downloaded results from Google
└── job_name.txt               ← Gemini job name (use with --resume)
```

### Success file (`successes/{stem}.json`)

```json
{
  "summary": "The petitioner challenged ...",
  "facts": "The assessee is a manufacturer ...",
  "issues": "Whether ITC can be claimed ...",
  "petitioner_arguments": "...",
  "respondent_arguments": "...",
  "analysis_of_law": "...",
  "precedent_analysis": "...",
  "courts_reasoning": "...",
  "conclusion": "Appeal allowed.",
  "ratio_decidendi": "..."
}
```

### Failure log (`failures/{stem}_{error_type}.log`)

```json
{
  "pdf": "/data/cases/scanned_doc.pdf",
  "error_type": "extraction_error",
  "error_detail": "No text extracted — may be a scanned/image-only PDF",
  "timestamp": "2025-03-01T10:23:45+00:00",
  "raw_response": ""
}
```

Error types:

| `error_type` | Cause |
|---|---|
| `extraction_error` | PDF couldn't be read (scanned, corrupt, missing) |
| `api_error` | Gemini returned an error for that request, or produced no output |
| `parse_error` | Model response wasn't valid JSON |
| `validation_error` | JSON was valid but missing required fields |

---

## Python API

```python
from pathlib import Path
from pipelines.gemini_batch_analyze import run_batch_analysis, discover_pdfs

pdf_paths = discover_pdfs(Path("/data/cases/"))

## AI Studio key (default)
stats = run_batch_analysis(
    pdf_paths=pdf_paths,
    output_dir=Path(".data/batch_analysis"),
    model="gemini-3-flash-preview",
    max_output_tokens=4096,
    poll_interval=180,
    display_name="my-batch-job",
)

# Vertex AI credentials
stats = run_batch_analysis(
    pdf_paths=pdf_paths,
    output_dir=Path(".data/batch_analysis"),
    vertexai=True,
    project="my-gcp-project",
    location="us-central1",
)

print(f"{stats.succeeded}/{stats.total} succeeded")
```

To resume programmatically:

```python
from pipelines.gemini_batch_analyze import resume_batch_analysis

stats = resume_batch_analysis(
    job_name="batches/abc123",
    output_dir=Path(".data/batch_analysis"),
    pdf_paths=pdf_paths,   # optional
)
```

---

## Important limits and behaviour

- **Max input file size**: 2 GB (the uploaded JSONL). With ~10 KB average per
  legal judgment text, this is roughly 200,000 PDFs per job.
- **Job expiry**: Jobs expire if pending or running for more than **48 hours**.
  After that the state becomes `JOB_STATE_EXPIRED` and results are lost.
- **Not idempotent**: Submitting the same JSONL twice creates two separate jobs.
  Always check `job_name.txt` before re-running.
- **Turnaround**: Google targets ~24 hours but jobs often complete in minutes for
  small batches.
- **Polling**: The default 60-second poll is conservative. For small batches you
  can pass `--poll-interval 10`.
- **Results file**: `batch_results.jsonl` is kept permanently so you can re-parse
  results without re-running the job.

---

## Monitoring your job

Open [Google AI Studio → Batch Jobs](https://aistudio.google.com/app/batches) to
see job status, progress, and estimated completion time. The display name you set
with `--display-name` appears here.

Alternatively, check via the SDK:

```python
from google import genai
client = genai.Client()
job = client.batches.get(name="batches/abc123")
print(job.state.name)   # JOB_STATE_RUNNING, JOB_STATE_SUCCEEDED, etc.
```
