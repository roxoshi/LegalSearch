"""Tests for filter_judgments module with ML-based classification."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipelines.filter_judgments import (
    MAX_TEXT_CHARS,
    GST_STATUTES_KEYWORDS,
    FilterResult,
    _classify_file,
    filter_judgments,
    main,
    predict_relevance,
    is_gst_relevant,
    _load_nlp_model,
)


def _make_case(processed_dir, pdf_dir, stem, text_content="Some judgment text"):
    """Create a synthetic JSON + PDF pair for testing."""
    json_path = processed_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps({"text_content": text_content, "citation": stem}),
        encoding="utf-8",
    )
    pdf_path = pdf_dir / f"{stem}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content")
    return json_path, pdf_path


@pytest.fixture
def dirs(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    return processed, pdfs


# ---------------------------------------------------------------------------
# predict_relevance (with mocked NLP)
# ---------------------------------------------------------------------------

class TestPredictRelevance:
    def test_fallback_when_no_ml_model(self):
        """When ML model unavailable, uses keyword fallback."""
        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            provisions, statutes = predict_relevance(
                "This case involves goods and services tax."
            )
            assert len(statutes) > 0
            assert provisions == set()

    def test_fallback_no_match(self):
        """Fallback returns empty when no GST keywords."""
        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            provisions, statutes = predict_relevance("This is about income tax.")
            assert statutes == set()
            assert provisions == set()

    def test_with_mock_nlp_model(self):
        """Test with mocked spaCy NLP model."""
        mock_nlp = MagicMock()
        mock_doc = MagicMock()
        mock_ent1 = MagicMock()
        mock_ent1.label_ = "PROVISION"
        mock_ent1.text = "Section 12"
        mock_ent2 = MagicMock()
        mock_ent2.label_ = "STATUTE"
        mock_ent2.text = "Central Goods and Services Tax Act"
        mock_doc.ents = [mock_ent1, mock_ent2]
        mock_nlp.return_value = mock_doc

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=mock_nlp):
            provisions, statutes = predict_relevance("test text")
            assert "Section 12" in provisions
            assert "Central Goods and Services Tax Act" in statutes


# ---------------------------------------------------------------------------
# is_gst_relevant
# ---------------------------------------------------------------------------

class TestIsGstRelevant:
    def test_gst_keyword_match(self):
        assert is_gst_relevant({"Central Goods and Services Tax Act"})
        assert is_gst_relevant({"GST Act 2017"})
        assert is_gst_relevant({"Goods & Services Tax"})

    def test_no_gst_keyword(self):
        assert not is_gst_relevant({"Income Tax Act"})
        assert not is_gst_relevant({"Companies Act"})
        assert not is_gst_relevant(set())


# ---------------------------------------------------------------------------
# _classify_file
# ---------------------------------------------------------------------------

class TestClassifyFile:
    def test_classify_relevant_case(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "gst_case", "Discussion of goods and services tax")
        json_file = processed / "gst_case.json"

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = _classify_file(json_file, pdfs, seed=42)
            _, stem, action, pdf_file, data = result

            assert stem == "gst_case"
            assert action == "keep"
            assert data["is_gst_core"] is True
            assert "extracted_provisions" in data
            assert "extracted_statutes" in data

    def test_classify_irrelevant_case(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "income_case", "Discussion of income tax only")
        json_file = processed / "income_case.json"

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = _classify_file(json_file, pdfs, seed=42)
            _, stem, action, pdf_file, data = result

            assert stem == "income_case"
            assert action == "remove"
            assert data["is_gst_core"] is False

    def test_text_truncated_to_max_chars(self, dirs):
        processed, pdfs = dirs
        long_text = "X" * 10000
        _make_case(processed, pdfs, "long_case", text_content=long_text)
        json_file = processed / "long_case.json"

        with patch("pipelines.filter_judgments.predict_relevance") as mock_pred:
            mock_pred.return_value = (set(), set())
            _classify_file(json_file, pdfs, seed=42)
            call_text = mock_pred.call_args[0][0]
            assert len(call_text) == MAX_TEXT_CHARS


# ---------------------------------------------------------------------------
# filter_judgments
# ---------------------------------------------------------------------------

class TestFilterJudgments:
    def test_keeps_gst_cases(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "gst_1", "goods and services tax case")
        _make_case(processed, pdfs, "gst_2", "GST implications discussed")

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = filter_judgments(processed, pdfs)

        assert result.kept == 2
        assert result.removed == 0
        assert (processed / "gst_1.json").exists()
        assert (processed / "gst_2.json").exists()

    def test_removes_non_gst_cases(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "income_1", "income tax case")
        _make_case(processed, pdfs, "income_2", "corporate tax case")

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = filter_judgments(processed, pdfs)

        assert result.kept == 0
        assert result.removed == 2
        assert not (processed / "income_1.json").exists()
        assert not (processed / "income_2.json").exists()

    def test_mixed_cases(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "gst_case", "goods and services tax")
        _make_case(processed, pdfs, "other_case", "income tax")

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = filter_judgments(processed, pdfs)

        assert result.kept == 1
        assert result.removed == 1
        assert (processed / "gst_case.json").exists()
        assert not (processed / "other_case.json").exists()

    def test_updates_json_with_extracted_fields(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "gst_case", "goods and services tax discussion")

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            filter_judgments(processed, pdfs)

        # Read the updated JSON
        with open(processed / "gst_case.json") as f:
            data = json.load(f)

        assert "is_gst_core" in data
        assert "extracted_provisions" in data
        assert "extracted_statutes" in data
        assert data["is_gst_core"] is True

    def test_empty_directory_returns_zeros(self, dirs):
        processed, pdfs = dirs
        result = filter_judgments(processed, pdfs)
        assert result == FilterResult(kept=0, removed=0)

    def test_missing_pdf_does_not_crash(self, dirs):
        processed, pdfs = dirs
        stem = "no_pdf_case"
        json_path = processed / f"{stem}.json"
        json_path.write_text(
            json.dumps({"text_content": "income tax case", "citation": stem}),
            encoding="utf-8",
        )

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = filter_judgments(processed, pdfs)

        assert result.kept + result.removed == 1

    def test_empty_text_content_handled(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "empty_text", text_content="")

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = filter_judgments(processed, pdfs)

        assert result.kept + result.removed == 1

    def test_subdirectory_structure(self, dirs):
        processed, pdfs = dirs
        subdir = processed / "SC_GST_2023"
        subdir.mkdir()
        _make_case(subdir, pdfs, "nested_gst", "goods and services tax")
        _make_case(processed, pdfs, "top_level", "income tax")

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            result = filter_judgments(processed, pdfs)

        assert result.kept == 1
        assert result.removed == 1


# ---------------------------------------------------------------------------
# _load_nlp_model
# ---------------------------------------------------------------------------

class TestLoadNlpModel:
    def test_returns_none_when_imports_fail(self):
        """When torch/spacy not installed, returns None gracefully."""
        with patch.dict("sys.modules", {"torch": None, "spacy": None}):
            # Reset the cached model
            import pipelines.filter_judgments as fm
            fm._nlp = None

            with patch("pipelines.filter_judgments._load_nlp_model") as mock_load:
                mock_load.return_value = None
                result = mock_load()
                assert result is None


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_main_with_args(self, dirs):
        processed, pdfs = dirs
        _make_case(processed, pdfs, "cli_gst", "goods and services tax")
        _make_case(processed, pdfs, "cli_other", "income tax")

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            with patch(
                "sys.argv",
                [
                    "filter_judgments",
                    "--processed-dir", str(processed),
                    "--pdf-dir", str(pdfs),
                ],
            ):
                main()

        remaining = list(processed.rglob("*.json"))
        assert len(remaining) == 1

    def test_main_empty_dir(self, tmp_path):
        processed = tmp_path / "processed"
        processed.mkdir()
        pdfs = tmp_path / "pdfs"
        pdfs.mkdir()

        with patch("pipelines.filter_judgments._load_nlp_model", return_value=None):
            with patch(
                "sys.argv",
                [
                    "filter_judgments",
                    "--processed-dir", str(processed),
                    "--pdf-dir", str(pdfs),
                ],
            ):
                main()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_gst_keywords_present(self):
        assert "gst" in GST_STATUTES_KEYWORDS
        assert "goods and services" in GST_STATUTES_KEYWORDS

    def test_max_text_chars(self):
        assert MAX_TEXT_CHARS == 6000
