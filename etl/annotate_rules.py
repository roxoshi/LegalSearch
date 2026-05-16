"""
Annotate GST rule sections with practitioner Q&A and summaries using Gemini.

Generates three plain-English questions (q1, q2, q3) and a one-sentence summary
for each rule section — used to improve semantic search quality at embedding time.

Default mode: synchronous (live API, immediate results, ~5-8 min for 243 sections).
Batch mode available with --batch flag (~50% cheaper but takes hours).

Usage:
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_rules            # sync (default)
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_rules --batch    # batch API
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_rules --load-db  # write to DB
    GOOGLE_API_KEY=xxx uv run python -m etl.annotate_rules --limit 10 # test run
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

RULES_DIR = Path(os.getenv("RULES_DIR", ".data/rules"))
OUT_JSON = RULES_DIR / "annotations.json"

MODEL = "models/gemini-2.5-flash"

SYSTEM_PROMPT = """\
You are a GST law expert annotating Indian GST Rules for a semantic search system.

Below is a JSON array of rule sections. For each section, generate annotations to improve semantic search.

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


def load_all_sections() -> list[dict]:
    sections = []
    for path in sorted(RULES_DIR.glob("*.json")):
        if path.name == "annotations.json":
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
    log.info("Total: %d rule sections", len(sections))
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


def _parse_response(text: str) -> list[dict]:
    """Parse JSON array from Gemini response."""
    try:
        result = json.loads(text.strip())
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


# ── Sync API (default) ────────────────────────────────────────────────────────

def run_sync(sections: list[dict], existing: dict, batch_size: int, model: str) -> dict:
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
    annotations = dict(existing)
    pending = [s for s in sections if str(s["content_id"]) not in annotations]
    log.info("%d sections pending annotation", len(pending))

    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
    total = 0
    parse_errors = 0

    for i, batch in enumerate(batches):
        prompt = SYSTEM_PROMPT + json.dumps(batch, ensure_ascii=False)
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            results = _parse_response(resp.text)
            if not results:
                log.warning("Batch %d/%d: empty/unparseable response", i + 1, len(batches))
                parse_errors += 1
                continue
            for item in results:
                cid = str(item.get("content_id", ""))
                if cid:
                    annotations[cid] = {k: item.get(k, "") for k in ("q1", "q2", "q3", "summary")}
                    total += 1
            save_annotations(annotations)
            log.info("Batch %d/%d done — %d annotated so far", i + 1, len(batches), total)
        except Exception as e:
            log.warning("Batch %d/%d error: %s", i + 1, len(batches), e)
            time.sleep(2)

    log.info("Sync complete. Annotated: %d  Parse errors: %d", total, parse_errors)
    return annotations


# ── Batch API ─────────────────────────────────────────────────────────────────

def run_batch(sections: list[dict], existing: dict, batch_size: int, model: str) -> dict:
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
    pending = [s for s in sections if str(s["content_id"]) not in existing]
    log.info("%d sections pending annotation", len(pending))
    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]

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

    terminal = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_PARTIALLY_SUCCEEDED"}
    while True:
        time.sleep(30)
        job = client.batches.get(name=job.name)
        state = job.state.value if hasattr(job.state, "value") else str(job.state)
        stats = job.completion_stats
        log.info("state=%-30s  success=%s  failed=%s", state,
                 stats.successful_count if stats else "?", stats.failed_count if stats else "?")
        if state in terminal:
            break

    if job.state.value not in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"):
        log.error("Batch job did not succeed: %s", job.state)
        sys.exit(1)

    annotations = dict(existing)
    for resp in job.dest.inlined_responses or []:
        if resp.error:
            continue
        try:
            results = _parse_response(resp.response.text.strip())
            for item in results:
                cid = str(item.get("content_id", ""))
                if cid:
                    annotations[cid] = {k: item.get(k, "") for k in ("q1", "q2", "q3", "summary")}
        except Exception as e:
            log.warning("Parse error: %s", e)

    save_annotations(annotations)
    log.info("Batch complete. %d total annotations.", len(annotations))
    return annotations


# ── Load into DB ──────────────────────────────────────────────────────────────

def load_annotations_to_db() -> None:
    if not OUT_JSON.exists():
        log.error("annotations.json not found — run annotation step first")
        sys.exit(1)

    with open(OUT_JSON) as f:
        annotations = json.load(f)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from etl.database import init_db
    try:
        from backend.app.models import Rule
    except ImportError:
        from app.models import Rule  # type: ignore

    SessionFactory = init_db()
    session = SessionFactory()
    try:
        updated = 0
        for content_id_str, ann in annotations.items():
            content_id = int(content_id_str)
            row = session.query(Rule).filter(Rule.content_id == content_id).first()
            if row is None:
                log.warning("content_id %s not found in rules table — skipping", content_id)
                continue
            row.q1 = ann.get("q1") or None
            row.q2 = ann.get("q2") or None
            row.q3 = ann.get("q3") or None
            row.ann_summary = ann.get("summary") or None
            updated += 1

        session.commit()
        log.info("Updated %d rules rows with annotations.", updated)
    except Exception:
        session.rollback()
        log.exception("Failed — rolled back.")
        sys.exit(1)
    finally:
        session.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate GST rule sections via Gemini")
    parser.add_argument("--batch", action="store_true", help="Use Batch API (cheaper, but takes hours)")
    parser.add_argument("--load-db", action="store_true", help="Write annotations.json into rules table")
    parser.add_argument("--model", default=MODEL, help=f"Gemini model (default: {MODEL})")
    parser.add_argument("--batch-size", type=int, default=20, help="Sections per request (default: 20)")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N sections (testing)")
    args = parser.parse_args()

    if args.load_db:
        load_annotations_to_db()
        return

    sections = load_all_sections()
    if args.limit:
        sections = sections[:args.limit]

    existing = load_existing_annotations()

    if args.batch:
        run_batch(sections, existing, args.batch_size, args.model)
    else:
        run_sync(sections, existing, args.batch_size, args.model)

    log.info("Done. Annotations saved to %s", OUT_JSON)
    log.info("Next step: uv run python -m etl.annotate_rules --load-db")


if __name__ == "__main__":
    main()
