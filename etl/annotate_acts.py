"""
Annotate GST act sections with practitioner Q&A and summaries using Gemini Batch API.

Generates three plain-English questions (q1, q2, q3) and a one-sentence summary
for each section — used to improve semantic search coverage in the acts library.

Default mode uses the Google AI Studio Batch API (async, ~50% cheaper than live calls).
All requests are submitted as a single inlined batch job — no GCS required.

Output: .data/acts/annotations.json   {content_id: {q1, q2, q3, summary}, ...}

Usage:
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_acts
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_acts --batch-size 20
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_acts --model models/gemini-2.5-flash
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_acts --jsonl-only
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_acts --parse-results output.jsonl
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ACTS_DIR = Path(os.getenv("ACTS_DIR", ".data/acts"))
OUT_JSON = ACTS_DIR / "annotations.json"
BATCH_INPUT_JSONL = ACTS_DIR / "batch_input.jsonl"

MODEL = "models/gemini-2.5-flash"

SYSTEM_PROMPT = """\
You are a GST law expert annotating Indian GST legislation for a semantic search system.

Below is a JSON array of act sections. For each section, generate annotations to improve semantic search.

Return a JSON array containing ONLY these fields for each section:
- "content_id": copied exactly from input
- "q1": plain-English question a GST practitioner would ask that this section directly answers
- "q2": another such question
- "q3": another such question
- "summary": one plain-English sentence (≤25 words) summarising what this section says

Rules:
- Return valid JSON only, no markdown, no explanation
- One output object per input object, in the same order
- Do not skip any sections

"""


# ── Data loading ───────────────────────────────────────────────────────────────

def load_all_sections() -> list[dict]:
    sections = []
    for path in sorted(ACTS_DIR.glob("*.json")):
        if path.name.startswith("batch_") or path.name == "annotations.json":
            continue
        with open(path) as f:
            data = json.load(f)
        for item in data:
            sections.append({
                "content_id": item["content_id"],
                "act_name": item.get("act_name", ""),
                "section_no": item.get("section_no", ""),
                "section_name": item.get("section_name", ""),
                "content": (item.get("content") or "").strip(),
            })
        log.info("Loaded %d sections from %s", len(data), path.name)
    log.info("Total: %d sections across all acts", len(sections))
    return sections


def load_existing_annotations() -> dict:
    if OUT_JSON.exists():
        with open(OUT_JSON) as f:
            data = json.load(f)
        log.info("Resuming: %d annotations already saved", len(data))
        return data
    return {}


def save_annotations(annotations: dict) -> None:
    OUT_JSON.write_text(json.dumps(annotations, indent=2, ensure_ascii=False))


def build_batches(sections: list[dict], existing: dict, batch_size: int) -> list[list[dict]]:
    pending = [s for s in sections if str(s["content_id"]) not in existing]
    log.info("%d sections pending annotation", len(pending))
    return [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]


# ── Batch API (default) ────────────────────────────────────────────────────────

def run_batch_api(batches: list[list[dict]], model: str) -> list[dict]:
    """Submit all batches as a single Gemini Batch API job (inlined, no GCS).

    Batch API is ~50% cheaper than live API calls. Job runs asynchronously;
    we poll every 30s until it succeeds or fails.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.error("google-genai not installed. Run: uv add google-genai")
        sys.exit(1)

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("Set GOOGLE_API_KEY environment variable")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # Build one InlinedRequest per batch of sections
    requests = []
    for batch in batches:
        prompt = SYSTEM_PROMPT + json.dumps(batch, ensure_ascii=False)
        requests.append(
            types.InlinedRequest(
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
        )

    log.info("Submitting batch job: %d requests, model=%s", len(requests), model)
    job = client.batches.create(model=model, src=requests)
    log.info("Batch job created: %s  (state=%s)", job.name, job.state)

    # Poll until terminal state
    terminal = {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_PARTIALLY_SUCCEEDED",
    }
    poll_interval = 30
    while True:
        time.sleep(poll_interval)
        job = client.batches.get(name=job.name)
        state = job.state.value if hasattr(job.state, "value") else str(job.state)
        stats = job.completion_stats
        log.info(
            "state=%-30s  success=%s  failed=%s  incomplete=%s",
            state,
            stats.successful_count if stats else "?",
            stats.failed_count if stats else "?",
            stats.incomplete_count if stats else "?",
        )
        if state in terminal:
            break

    if job.state.value not in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"):
        log.error("Batch job did not succeed: %s — %s", job.state, job.error)
        sys.exit(1)

    # Parse inlined responses (same order as requests)
    all_results: list[dict] = []
    for i, resp in enumerate(job.dest.inlined_responses or []):
        if resp.error:
            log.warning("Request %d failed: %s", i + 1, resp.error)
            continue
        try:
            text = resp.response.text.strip()
            results = json.loads(text)
            if isinstance(results, list):
                all_results.extend(results)
            else:
                log.warning("Request %d: expected list, got %s", i + 1, type(results))
        except (json.JSONDecodeError, AttributeError) as exc:
            log.warning("Request %d: parse error — %s", i + 1, exc)

    log.info("Batch job complete. Parsed %d annotations.", len(all_results))
    return all_results


# ── Manual JSONL for AI Studio Batch UI ───────────────────────────────────────

def write_batch_input_jsonl(batches: list[list[dict]]) -> None:
    """Write JSONL for manual upload to AI Studio Batch UI.

    Each line is one self-contained request. Upload at:
      https://aistudio.google.com/ → Batch jobs → New batch
    Then run: uv run python -m etl.annotate_acts --parse-results <output.jsonl>
    """
    lines = []
    for batch in batches:
        prompt = SYSTEM_PROMPT + json.dumps(batch, ensure_ascii=False)
        lines.append(json.dumps({
            "request": {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
            }
        }, ensure_ascii=False))

    BATCH_INPUT_JSONL.write_text("\n".join(lines) + "\n")
    log.info("Wrote %d requests to %s", len(lines), BATCH_INPUT_JSONL)
    log.info("Upload at https://aistudio.google.com/ → Batch jobs → New batch")
    log.info("After download: uv run python -m etl.annotate_acts --parse-results <output.jsonl>")


# ── Parse downloaded AI Studio Batch output ───────────────────────────────────

def parse_batch_output(output_jsonl: Path, existing: dict) -> dict:
    """Parse the JSONL output downloaded from AI Studio Batch UI."""
    annotations = dict(existing)
    with open(output_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            try:
                text = obj["response"]["candidates"][0]["content"]["parts"][0]["text"]
                results = json.loads(text.strip())
                for item in results:
                    cid = str(item["content_id"])
                    annotations[cid] = {k: item.get(k, "") for k in ("q1", "q2", "q3", "summary")}
            except (KeyError, json.JSONDecodeError) as exc:
                log.warning("Skipping unparseable line: %s", exc)
    log.info("Parsed %d total annotations", len(annotations))
    return annotations


# ── Load annotations into DB ──────────────────────────────────────────────────

def load_annotations_to_db() -> None:
    """Write q1/q2/q3/ann_summary from annotations.json into the acts table."""
    if not OUT_JSON.exists():
        log.error("annotations.json not found — run annotation step first")
        sys.exit(1)

    with open(OUT_JSON) as f:
        annotations = json.load(f)

    try:
        from etl.database import init_db
        from backend.app.models import Act
    except ImportError:
        from database import init_db  # type: ignore
        from app.models import Act    # type: ignore

    SessionFactory = init_db()
    session = SessionFactory()
    try:
        updated = 0
        for content_id_str, ann in annotations.items():
            content_id = int(content_id_str)
            row = session.query(Act).filter(Act.content_id == content_id).first()
            if row is None:
                log.warning("content_id %s not found in acts table — skipping", content_id)
                continue
            row.q1 = ann.get("q1") or None
            row.q2 = ann.get("q2") or None
            row.q3 = ann.get("q3") or None
            row.ann_summary = ann.get("summary") or None
            updated += 1

        session.commit()
        log.info("Updated %d acts rows with annotations.", updated)
    except Exception:
        session.rollback()
        log.exception("Failed — rolled back.")
        sys.exit(1)
    finally:
        session.close()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate GST act sections via Gemini Batch API")
    parser.add_argument("--model", default=MODEL, help=f"Gemini model (default: {MODEL})")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="Sections per request (default: 20)")
    parser.add_argument("--jsonl-only", action="store_true",
                        help="Write batch_input.jsonl for manual AI Studio upload and exit")
    parser.add_argument("--parse-results", metavar="OUTPUT_JSONL",
                        help="Parse downloaded AI Studio Batch output JSONL")
    parser.add_argument("--load-db", action="store_true",
                        help="Load annotations.json into the acts table (run after annotating)")
    args = parser.parse_args()

    if args.load_db:
        load_annotations_to_db()
        return

    sections = load_all_sections()
    existing = load_existing_annotations()

    if args.parse_results:
        output_path = Path(args.parse_results)
        if not output_path.exists():
            log.error("File not found: %s", output_path)
            sys.exit(1)
        annotations = parse_batch_output(output_path, existing)
        save_annotations(annotations)
        log.info("Saved %d annotations to %s", len(annotations), OUT_JSON)
        return

    batches = build_batches(sections, existing, args.batch_size)
    if not batches:
        log.info("All sections already annotated. Nothing to do.")
        return

    log.info("%d batches of up to %d sections each", len(batches), args.batch_size)

    if args.jsonl_only:
        write_batch_input_jsonl(batches)
        return

    # Default: use Batch API
    all_results = run_batch_api(batches, args.model)

    annotations = dict(existing)
    for item in all_results:
        cid = str(item.get("content_id", ""))
        if cid:
            annotations[cid] = {k: item.get(k, "") for k in ("q1", "q2", "q3", "summary")}

    save_annotations(annotations)
    log.info("Done. %d / %d sections annotated. Saved to %s",
             len(annotations), len(sections), OUT_JSON)


if __name__ == "__main__":
    main()
