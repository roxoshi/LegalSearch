"""Salvage LLM analysis failures that can be fixed without re-calling the LLM.

Two fix strategies:
  1. parse_error / "Extra data" — strip the trailing stray `}` and re-parse.
  2. validation_error / missing courts_reasoning — rename `court_reasoning` field.

Usage:
    python -m pipelines.salvage_failures --failures-dir .data/batch_analysis/failures \
                                         --successes-dir .data/batch_analysis/successes
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from pipelines.llm_analyze import CaseLawAnalysis

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

NEED_RERUN = {
    "HCBM020275232019",
    "HCMA010175882025",
    "HCMA011341012025",
    "HCMA011996182022",
    "KAHC010708752024",
    "GJHC240428152019",
    "RJHC010290702023",
}


def unwrap_array(data: object) -> dict | None:
    """If data is a list, return the first dict element; otherwise return as-is."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def try_fix_parse_error(raw: str) -> dict | None:
    """Try progressively looser fixes for JSON parse errors."""
    # 1. Plain parse (maybe it's already valid, possibly array-wrapped)
    try:
        return unwrap_array(json.loads(raw))
    except json.JSONDecodeError:
        pass

    # 2. Strip trailing whitespace/stray bracket or brace
    stripped = raw.strip()
    for tail in ("]", "}", "]\n]", "}\n}"):
        if stripped.endswith(tail):
            candidate = stripped[: len(stripped) - len(tail)].rstrip()
            # Re-close properly
            for close in ("]", "}"):
                try:
                    return unwrap_array(json.loads(candidate + close))
                except json.JSONDecodeError:
                    pass

    # 3. Keep only up to the first complete top-level object
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[: i + 1])
                except json.JSONDecodeError:
                    break
    return None


def try_fix_validation_error(raw: str, error_detail: str) -> dict | None:
    """Rename court_reasoning → courts_reasoning if that's the only issue."""
    if "courts_reasoning" not in error_detail:
        return None
    try:
        data = unwrap_array(json.loads(raw))
    except json.JSONDecodeError:
        data = None
    if data is None:
        # Try fixing parse first, then rename
        data = try_fix_parse_error(raw)
    if data is None:
        return None
    if "court_reasoning" in data and "courts_reasoning" not in data:
        data["courts_reasoning"] = data.pop("court_reasoning")
        return data
    return None


def salvage(failures_dir: Path, successes_dir: Path) -> None:
    successes_dir.mkdir(parents=True, exist_ok=True)

    saved = skipped = failed = 0

    for log_file in sorted(failures_dir.glob("*.log")):
        stem = log_file.stem  # e.g. BRHC010491462020_parse_error
        case_id = stem.rsplit("_", 2)[0]  # strip _parse_error / _validation_error

        if case_id in NEED_RERUN:
            logger.info(f"SKIP (needs rerun): {case_id}")
            skipped += 1
            continue

        success_path = successes_dir / f"{case_id}.json"
        if success_path.exists():
            logger.info(f"SKIP (already exists): {case_id}")
            skipped += 1
            continue

        try:
            failure = json.loads(log_file.read_text())
        except json.JSONDecodeError as e:
            logger.warning(f"FAIL (bad log file): {case_id}: {e}")
            failed += 1
            continue

        raw = failure.get("raw_response", "")
        error_type = failure.get("error_type", "")
        error_detail = failure.get("error_detail", "")

        fixed_data: dict | None = None

        if error_type == "parse_error":
            fixed_data = try_fix_parse_error(raw)
        elif error_type == "validation_error":
            fixed_data = try_fix_validation_error(raw, error_detail)

        if fixed_data is None:
            logger.warning(f"FAIL (could not fix): {case_id}")
            failed += 1
            continue

        try:
            analysis = CaseLawAnalysis(**fixed_data)
        except ValidationError as e:
            logger.warning(f"FAIL (still invalid after fix): {case_id}: {e}")
            failed += 1
            continue

        success_path.write_text(analysis.model_dump_json(indent=2))
        logger.info(f"SAVED: {case_id}")
        saved += 1

    print(f"\nDone. Saved: {saved}  Skipped: {skipped}  Failed: {failed}")
    if failed:
        print("Check WARNING lines above for details.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Salvage fixable LLM analysis failures.")
    parser.add_argument("--failures-dir", default=".data/batch_analysis/failures", type=Path)
    parser.add_argument("--successes-dir", default=".data/batch_analysis/successes", type=Path)
    args = parser.parse_args()
    salvage(args.failures_dir, args.successes_dir)


if __name__ == "__main__":
    main()
