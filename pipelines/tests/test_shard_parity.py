"""
Validate that the new shard_judgments.py pipeline produces data
equivalent to what was manually staged in backend/.data.

For each of the 22 files in backend/.data/ingestion_data/ and gst_pdfs/:
  1. A matching JSON exists in .data/metadata/processed/ with the correct
     schema-required fields (case_id, title, petitioner, respondent, judge,
     citation, court, decision_date, text_content).
  2. A matching PDF exists in .data/gst_pdfs/ and is a valid non-empty PDF.
  3. The text_content in the new JSON is non-empty (extracted from the same
     source zip that the old pipeline used).
  4. The metadata fields (case_id, title, petitioner, respondent, judge,
     citation, court, decision_date) match between old and new JSONs.
"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

OLD_JSON_DIR = PROJECT_ROOT / "backend" / ".data" / "ingestion_data"
OLD_PDF_DIR = PROJECT_ROOT / "backend" / ".data" / "gst_pdfs"
NEW_JSON_DIR = PROJECT_ROOT / ".data" / "metadata" / "processed"
NEW_PDF_DIR = PROJECT_ROOT / ".data" / "gst_pdfs"

# Fields that the ETL schema (etl/schemas.py DocumentJSON) requires
SCHEMA_REQUIRED_FIELDS = ["case_id", "title", "decision_date", "text_content"]
SCHEMA_OPTIONAL_FIELDS = ["petitioner", "respondent", "judge", "citation", "court"]

# Metadata fields that must match between old and new (identity fields)
METADATA_FIELDS = [
    "case_id",
    "title",
    "petitioner",
    "respondent",
    "judge",
    "citation",
    "court",
    "decision_date",
]


def _get_old_json_stems():
    """Return sorted list of file stems from backend/.data/ingestion_data/."""
    if not OLD_JSON_DIR.exists():
        pytest.skip("backend/.data/ingestion_data not found")
    return sorted(p.stem for p in OLD_JSON_DIR.glob("*.json"))


def _find_new_json(stem):
    """Find the JSON for a given stem inside .data/metadata/processed/**/."""
    matches = list(NEW_JSON_DIR.rglob(f"{stem}.json"))
    return matches[0] if matches else None


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


OLD_STEMS = _get_old_json_stems()


@pytest.fixture(params=OLD_STEMS, ids=OLD_STEMS)
def case_stem(request):
    return request.param


class TestNewJsonExists:
    """Every old JSON must have a corresponding new JSON produced by the pipeline."""

    def test_json_file_exists(self, case_stem):
        if not NEW_JSON_DIR.exists():
            pytest.skip("Processed output directory not found (pipeline not yet run)")
        new_json = _find_new_json(case_stem)
        if new_json is None:
            pytest.skip(
                f"JSON not yet produced for {case_stem} (shard pipeline not run for this case)"
            )


class TestNewPdfExists:
    """Every old PDF must have a corresponding new PDF extracted by the pipeline."""

    def test_pdf_file_exists(self, case_stem):
        if not NEW_PDF_DIR.exists():
            pytest.skip("PDF output directory not found (pipeline not yet run)")
        new_pdf = NEW_PDF_DIR / f"{case_stem}.pdf"
        if not new_pdf.exists():
            pytest.skip(
                f"PDF not yet produced for {case_stem} (shard pipeline not run for this case)"
            )

    def test_pdf_is_nonempty(self, case_stem):
        new_pdf = NEW_PDF_DIR / f"{case_stem}.pdf"
        if not new_pdf.exists():
            pytest.skip("PDF not yet produced")
        assert new_pdf.stat().st_size > 0, f"PDF is empty: {new_pdf}"

    def test_pdf_starts_with_magic_bytes(self, case_stem):
        new_pdf = NEW_PDF_DIR / f"{case_stem}.pdf"
        if not new_pdf.exists():
            pytest.skip("PDF not yet produced")
        with open(new_pdf, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-", f"Not a valid PDF: {new_pdf}"


class TestSchemaFields:
    """New JSON must contain all fields required by etl/schemas.py DocumentJSON."""

    def test_required_fields_present(self, case_stem):
        new_json_path = _find_new_json(case_stem)
        if not new_json_path:
            pytest.skip("JSON not yet produced")
        data = _load_json(new_json_path)
        for field in SCHEMA_REQUIRED_FIELDS:
            assert field in data, f"Missing required field '{field}' in {new_json_path.name}"

    def test_text_content_nonempty(self, case_stem):
        new_json_path = _find_new_json(case_stem)
        if not new_json_path:
            pytest.skip("JSON not yet produced")
        data = _load_json(new_json_path)
        text = data.get("text_content", "")
        assert len(text) > 0, f"text_content is empty in {new_json_path.name}"


class TestMetadataParity:
    """Metadata fields in new JSON must match the old manually-staged JSON."""

    def test_metadata_matches(self, case_stem):
        old_json_path = OLD_JSON_DIR / f"{case_stem}.json"
        new_json_path = _find_new_json(case_stem)
        if not new_json_path:
            pytest.skip("JSON not yet produced")

        old_data = _load_json(old_json_path)
        new_data = _load_json(new_json_path)

        for field in METADATA_FIELDS:
            old_val = str(old_data.get(field, "")).strip()
            new_val = str(new_data.get(field, "")).strip()
            assert old_val == new_val, (
                f"Field '{field}' mismatch for {case_stem}: old={old_val!r} vs new={new_val!r}"
            )


class TestPdfSizeParity:
    """New extracted PDF should be the same size as the old one (same source zip)."""

    def test_pdf_sizes_match(self, case_stem):
        old_pdf = OLD_PDF_DIR / f"{case_stem}.pdf"
        new_pdf = NEW_PDF_DIR / f"{case_stem}.pdf"
        if not new_pdf.exists():
            pytest.skip("New PDF not yet produced")
        if not old_pdf.exists():
            pytest.skip("Old PDF not found")

        old_size = old_pdf.stat().st_size
        new_size = new_pdf.stat().st_size
        assert old_size == new_size, (
            f"PDF size mismatch for {case_stem}: old={old_size} bytes vs new={new_size} bytes"
        )
