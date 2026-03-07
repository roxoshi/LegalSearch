"""Tests for pipelines.llm_analyze."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from pipelines.llm_analyze import (
    ERROR_API,
    ERROR_EXTRACTION,
    ERROR_PARSE,
    ERROR_VALIDATION,
    AnalysisFailure,
    AnalysisSuccess,
    CaseLawAnalysis,
    RunStats,
    analyze_pdf,
    discover_pdfs,
    extract_text,
    parse_json_response,
    run_analysis,
    write_failure,
    write_success,
)
from pipelines.llm_providers import LLMProvider

# ── Shared test data ───────────────────────────────────────────────────────────

VALID_ANALYSIS_DICT = {
    "summary": "The court allowed the taxpayer's appeal against denial of ITC.",
    "facts": "Petitioner was denied input tax credit on zero-rated supplies.",
    "issues": "Whether ITC is available on inputs used in zero-rated supplies.",
    "petitioner_arguments": "Section 16 of CGST Act entitles the claim.",
    "respondent_arguments": "Petitioner failed to file GSTR-3B within time.",
    "analysis_of_law": "Section 16 of CGST Act read with Rule 89 of CGST Rules.",
    "precedent_analysis": "Not Mentioned",
    "courts_reasoning": "Portal technical glitch cannot defeat a statutory right.",
    "conclusion": "Appeal allowed with costs.",
    "ratio_decidendi": "Technical glitches on the GST portal do not extinguish ITC rights.",
}

VALID_ANALYSIS_JSON = json.dumps(VALID_ANALYSIS_DICT)


# ── Mock provider ──────────────────────────────────────────────────────────────


class MockProvider(LLMProvider):
    """Configurable mock — returns a fixed string or raises on demand."""

    def __init__(self, response: str = "", raises: Exception | None = None):
        self._response = response
        self._raises = raises

    @property
    def name(self) -> str:
        return "mock/test-model"

    def complete(self, case_text: str) -> str:
        if self._raises:
            raise self._raises
        return self._response


# ── PDF fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def pdf_with_text(tmp_path: Path) -> Path:
    """A real PDF containing extractable text."""
    path = tmp_path / "judgment.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 100),
        "IN THE HIGH COURT OF DELHI\n"
        "W.P.(C) 1234/2024\n"
        "ABC Ltd vs Department of Revenue\n"
        "The appeal is allowed. ITC claim upheld.",
    )
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def pdf_without_text(tmp_path: Path) -> Path:
    """A valid PDF with no text (image-only / blank)."""
    path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def pdf_dir(tmp_path: Path) -> Path:
    """A directory containing two PDFs."""
    for name in ("alpha.pdf", "beta.pdf"):
        p = tmp_path / name
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), f"Content of {name}")
        doc.save(str(p))
        doc.close()
    return tmp_path


# ── CaseLawAnalysis (Pydantic model) ──────────────────────────────────────────


class TestCaseLawAnalysis:
    def test_valid_dict_creates_model(self):
        analysis = CaseLawAnalysis(**VALID_ANALYSIS_DICT)
        assert analysis.summary == VALID_ANALYSIS_DICT["summary"]
        assert analysis.ratio_decidendi == VALID_ANALYSIS_DICT["ratio_decidendi"]

    def test_all_ten_fields_present(self):
        analysis = CaseLawAnalysis(**VALID_ANALYSIS_DICT)
        for field in (
            "summary",
            "facts",
            "issues",
            "petitioner_arguments",
            "respondent_arguments",
            "analysis_of_law",
            "precedent_analysis",
            "courts_reasoning",
            "conclusion",
            "ratio_decidendi",
        ):
            assert hasattr(analysis, field)

    def test_missing_required_field_raises_validation_error(self):
        from pydantic import ValidationError

        incomplete = {k: v for k, v in VALID_ANALYSIS_DICT.items() if k != "summary"}
        with pytest.raises(ValidationError):
            CaseLawAnalysis(**incomplete)

    def test_extra_fields_are_ignored(self):
        """model_config extra='ignore' must silently drop unknown keys."""
        extra = {**VALID_ANALYSIS_DICT, "hallucinated_field": "should be ignored"}
        analysis = CaseLawAnalysis(**extra)
        assert not hasattr(analysis, "hallucinated_field")

    def test_model_dump_json_produces_valid_json(self):
        analysis = CaseLawAnalysis(**VALID_ANALYSIS_DICT)
        parsed = json.loads(analysis.model_dump_json())
        assert parsed["summary"] == VALID_ANALYSIS_DICT["summary"]

    def test_not_mentioned_is_valid_value(self):
        """PROMPT.md instructs the LLM to use 'Not Mentioned' for absent fields."""
        data = {**VALID_ANALYSIS_DICT, "precedent_analysis": "Not Mentioned"}
        analysis = CaseLawAnalysis(**data)
        assert analysis.precedent_analysis == "Not Mentioned"


# ── parse_json_response ────────────────────────────────────────────────────────


class TestParseJsonResponse:
    def test_plain_json_string(self):
        result = parse_json_response(VALID_ANALYSIS_JSON)
        assert result["summary"] == VALID_ANALYSIS_DICT["summary"]

    def test_strips_json_code_fence(self):
        fenced = f"```json\n{VALID_ANALYSIS_JSON}\n```"
        result = parse_json_response(fenced)
        assert result["conclusion"] == VALID_ANALYSIS_DICT["conclusion"]

    def test_strips_plain_code_fence(self):
        fenced = f"```\n{VALID_ANALYSIS_JSON}\n```"
        result = parse_json_response(fenced)
        assert result["facts"] == VALID_ANALYSIS_DICT["facts"]

    def test_handles_leading_trailing_whitespace(self):
        padded = f"   \n{VALID_ANALYSIS_JSON}\n   "
        result = parse_json_response(padded)
        assert result["issues"] == VALID_ANALYSIS_DICT["issues"]

    def test_invalid_json_raises_json_decode_error(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_response("this is not json at all")

    def test_invalid_json_inside_fence_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_response("```json\nnot valid json\n```")

    def test_returns_dict(self):
        result = parse_json_response(VALID_ANALYSIS_JSON)
        assert isinstance(result, dict)


# ── extract_text ───────────────────────────────────────────────────────────────


class TestExtractText:
    def test_extracts_text_from_pdf(self, pdf_with_text):
        text = extract_text(pdf_with_text)
        assert "HIGH COURT" in text
        assert "ABC Ltd" in text

    def test_returns_string(self, pdf_with_text):
        assert isinstance(extract_text(pdf_with_text), str)

    def test_nonexistent_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="PDF not found"):
            extract_text(tmp_path / "missing.pdf")

    def test_blank_pdf_raises_value_error(self, pdf_without_text):
        with pytest.raises(ValueError, match="No text extracted"):
            extract_text(pdf_without_text)


# ── analyze_pdf ────────────────────────────────────────────────────────────────


class TestAnalyzePdf:
    def test_success_path(self, pdf_with_text):
        provider = MockProvider(response=VALID_ANALYSIS_JSON)
        result = analyze_pdf(pdf_with_text, provider)

        assert isinstance(result, AnalysisSuccess)
        assert result.pdf_path == pdf_with_text
        assert result.analysis.summary == VALID_ANALYSIS_DICT["summary"]
        assert result.raw_response == VALID_ANALYSIS_JSON

    def test_extraction_error_on_missing_pdf(self, tmp_path):
        provider = MockProvider(response=VALID_ANALYSIS_JSON)
        result = analyze_pdf(tmp_path / "ghost.pdf", provider)

        assert isinstance(result, AnalysisFailure)
        assert result.error_type == ERROR_EXTRACTION
        assert "ghost.pdf" in result.error_detail

    def test_extraction_error_on_blank_pdf(self, pdf_without_text):
        provider = MockProvider(response=VALID_ANALYSIS_JSON)
        result = analyze_pdf(pdf_without_text, provider)

        assert isinstance(result, AnalysisFailure)
        assert result.error_type == ERROR_EXTRACTION

    def test_api_error_captured(self, pdf_with_text):
        provider = MockProvider(raises=ConnectionError("API timeout"))
        result = analyze_pdf(pdf_with_text, provider)

        assert isinstance(result, AnalysisFailure)
        assert result.error_type == ERROR_API
        assert "API timeout" in result.error_detail

    def test_parse_error_on_non_json_response(self, pdf_with_text):
        provider = MockProvider(response="I cannot analyze this document.")
        result = analyze_pdf(pdf_with_text, provider)

        assert isinstance(result, AnalysisFailure)
        assert result.error_type == ERROR_PARSE
        assert result.raw_response == "I cannot analyze this document."

    def test_validation_error_on_missing_field(self, pdf_with_text):
        incomplete = {k: v for k, v in VALID_ANALYSIS_DICT.items() if k != "summary"}
        provider = MockProvider(response=json.dumps(incomplete))
        result = analyze_pdf(pdf_with_text, provider)

        assert isinstance(result, AnalysisFailure)
        assert result.error_type == ERROR_VALIDATION
        assert "summary" in result.error_detail  # Pydantic error JSON names the field

    def test_validation_error_preserves_raw_response(self, pdf_with_text):
        incomplete = {k: v for k, v in VALID_ANALYSIS_DICT.items() if k != "ratio_decidendi"}
        raw = json.dumps(incomplete)
        provider = MockProvider(response=raw)
        result = analyze_pdf(pdf_with_text, provider)

        assert isinstance(result, AnalysisFailure)
        assert result.raw_response == raw

    def test_fenced_json_is_parsed_correctly(self, pdf_with_text):
        fenced = f"```json\n{VALID_ANALYSIS_JSON}\n```"
        provider = MockProvider(response=fenced)
        result = analyze_pdf(pdf_with_text, provider)

        assert isinstance(result, AnalysisSuccess)
        assert result.analysis.conclusion == VALID_ANALYSIS_DICT["conclusion"]


# ── write_success ──────────────────────────────────────────────────────────────


class TestWriteSuccess:
    def test_creates_json_file_with_stem_name(self, tmp_path, pdf_with_text):
        analysis = CaseLawAnalysis(**VALID_ANALYSIS_DICT)
        result = AnalysisSuccess(
            pdf_path=pdf_with_text, analysis=analysis, raw_response=VALID_ANALYSIS_JSON
        )
        out = write_success(result, tmp_path)

        assert out.exists()
        assert out.name == f"{pdf_with_text.stem}.json"

    def test_output_is_valid_json(self, tmp_path, pdf_with_text):
        analysis = CaseLawAnalysis(**VALID_ANALYSIS_DICT)
        result = AnalysisSuccess(
            pdf_path=pdf_with_text, analysis=analysis, raw_response=VALID_ANALYSIS_JSON
        )
        out = write_success(result, tmp_path)
        parsed = json.loads(out.read_text())

        assert parsed["summary"] == VALID_ANALYSIS_DICT["summary"]
        assert parsed["ratio_decidendi"] == VALID_ANALYSIS_DICT["ratio_decidendi"]

    def test_output_contains_all_ten_fields(self, tmp_path, pdf_with_text):
        analysis = CaseLawAnalysis(**VALID_ANALYSIS_DICT)
        result = AnalysisSuccess(
            pdf_path=pdf_with_text, analysis=analysis, raw_response=""
        )
        out = write_success(result, tmp_path)
        parsed = json.loads(out.read_text())

        assert set(parsed.keys()) == set(VALID_ANALYSIS_DICT.keys())


# ── write_failure ──────────────────────────────────────────────────────────────


class TestWriteFailure:
    def test_creates_log_file_with_error_type_suffix(self, tmp_path, pdf_with_text):
        result = AnalysisFailure(
            pdf_path=pdf_with_text,
            error_type=ERROR_API,
            error_detail="Connection refused",
            raw_response="",
        )
        log = write_failure(result, tmp_path)

        assert log.exists()
        assert log.name == f"{pdf_with_text.stem}_{ERROR_API}.log"

    def test_log_contains_required_keys(self, tmp_path, pdf_with_text):
        result = AnalysisFailure(
            pdf_path=pdf_with_text,
            error_type=ERROR_PARSE,
            error_detail="Expecting value",
            raw_response="not json",
        )
        log = write_failure(result, tmp_path)
        payload = json.loads(log.read_text())

        assert payload["error_type"] == ERROR_PARSE
        assert payload["error_detail"] == "Expecting value"
        assert payload["raw_response"] == "not json"
        assert "timestamp" in payload
        assert "pdf" in payload

    def test_log_for_each_error_type(self, tmp_path, pdf_with_text):
        for error_type in (ERROR_EXTRACTION, ERROR_API, ERROR_PARSE, ERROR_VALIDATION):
            result = AnalysisFailure(
                pdf_path=pdf_with_text,
                error_type=error_type,
                error_detail="test",
            )
            log = write_failure(result, tmp_path)
            assert error_type in log.name


# ── discover_pdfs ──────────────────────────────────────────────────────────────


class TestDiscoverPdfs:
    def test_directory_returns_all_pdfs_sorted(self, pdf_dir):
        pdfs = discover_pdfs(pdf_dir)
        assert len(pdfs) == 2
        assert pdfs[0].name == "alpha.pdf"
        assert pdfs[1].name == "beta.pdf"

    def test_single_pdf_file_returns_list_of_one(self, pdf_with_text):
        result = discover_pdfs(pdf_with_text)
        assert result == [pdf_with_text]

    def test_empty_directory_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="No PDF files found"):
            discover_pdfs(tmp_path)

    def test_nonexistent_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            discover_pdfs(tmp_path / "nope.pdf")

    def test_non_pdf_file_raises_value_error(self, tmp_path):
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        with pytest.raises(ValueError, match="must be a .pdf"):
            discover_pdfs(txt)

    def test_discovers_pdfs_in_subdirectories(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        p = sub / "nested.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(p))
        doc.close()

        result = discover_pdfs(tmp_path)
        assert any(r.name == "nested.pdf" for r in result)


# ── run_analysis ───────────────────────────────────────────────────────────────


class TestRunAnalysis:
    def test_all_success_returns_correct_stats(self, pdf_dir, tmp_path):
        provider = MockProvider(response=VALID_ANALYSIS_JSON)
        pdfs = discover_pdfs(pdf_dir)
        stats = run_analysis(pdfs, provider, output_dir=tmp_path / "out")

        assert stats.total == 2
        assert stats.succeeded == 2
        assert stats.failed == 0

    def test_success_files_are_created(self, pdf_dir, tmp_path):
        provider = MockProvider(response=VALID_ANALYSIS_JSON)
        pdfs = discover_pdfs(pdf_dir)
        out_dir = tmp_path / "out"
        run_analysis(pdfs, provider, output_dir=out_dir)

        success_files = list((out_dir / "successes").glob("*.json"))
        assert len(success_files) == 2

    def test_api_failure_recorded_in_stats(self, pdf_dir, tmp_path):
        provider = MockProvider(raises=RuntimeError("rate limit"))
        pdfs = discover_pdfs(pdf_dir)
        stats = run_analysis(pdfs, provider, output_dir=tmp_path / "out")

        assert stats.failed_api == 2
        assert stats.succeeded == 0

    def test_failure_log_files_are_created(self, pdf_dir, tmp_path):
        provider = MockProvider(raises=RuntimeError("rate limit"))
        pdfs = discover_pdfs(pdf_dir)
        out_dir = tmp_path / "out"
        run_analysis(pdfs, provider, output_dir=out_dir)

        log_files = list((out_dir / "failures").glob("*.log"))
        assert len(log_files) == 2

    def test_mixed_results_counted_correctly(self, tmp_path):
        # Create two PDFs: one with text, one blank (will fail extraction)
        good_pdf = tmp_path / "good.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Valid case text")
        doc.save(str(good_pdf))
        doc.close()

        blank_pdf = tmp_path / "blank.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(blank_pdf))
        doc.close()

        provider = MockProvider(response=VALID_ANALYSIS_JSON)
        out_dir = tmp_path / "out"
        stats = run_analysis([good_pdf, blank_pdf], provider, output_dir=out_dir)

        assert stats.total == 2
        assert stats.succeeded == 1
        assert stats.failed_extraction == 1
        assert stats.failed == 1

    def test_output_directories_created_automatically(self, pdf_dir, tmp_path):
        provider = MockProvider(response=VALID_ANALYSIS_JSON)
        out_dir = tmp_path / "nested" / "output"
        run_analysis(discover_pdfs(pdf_dir), provider, output_dir=out_dir)

        assert (out_dir / "successes").is_dir()
        assert (out_dir / "failures").is_dir()

    def test_parse_error_stats(self, pdf_dir, tmp_path):
        provider = MockProvider(response="not valid json at all")
        stats = run_analysis(
            discover_pdfs(pdf_dir), provider, output_dir=tmp_path / "out"
        )
        assert stats.failed_parse == 2

    def test_validation_error_stats(self, pdf_dir, tmp_path):
        incomplete = {k: v for k, v in VALID_ANALYSIS_DICT.items() if k != "summary"}
        provider = MockProvider(response=json.dumps(incomplete))
        stats = run_analysis(
            discover_pdfs(pdf_dir), provider, output_dir=tmp_path / "out"
        )
        assert stats.failed_validation == 2


# ── RunStats ───────────────────────────────────────────────────────────────────


class TestRunStats:
    def test_failed_property_sums_all_failure_categories(self):
        s = RunStats(
            total=10,
            succeeded=4,
            failed_extraction=1,
            failed_api=2,
            failed_parse=1,
            failed_validation=2,
        )
        assert s.failed == 6

    def test_failed_is_zero_when_all_succeed(self):
        s = RunStats(total=5, succeeded=5)
        assert s.failed == 0
