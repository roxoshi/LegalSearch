"""Tests for convert_pdf module."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipelines.convert_pdf import (
    HEADING_KEYWORDS,
    NON_HEADING_PATTERNS,
    analyze_marker_columns,
    clean_text,
    fix_text_artifacts,
    is_header_footer,
    is_margin_junk,
    is_valid_heading,
    should_merge_blocks,
    merge_line_spans,
    extract_blocks_from_page,
    merge_paragraphs,
    build_html,
    convert_pdf_to_html,
    convert_bulk_htmls,
    main,
)


def _make_block(text, bbox=(60, 200, 400, 220), bold=False):
    """Helper to create a properly structured PDF block for mocking."""
    return {
        "type": 0,
        "bbox": bbox,
        "lines": [
            {
                "spans": [
                    {
                        "text": text,
                        "flags": 16 if bold else 0,
                        "font": "Bold" if bold else "Regular",
                    }
                ]
            }
        ]
    }


def _make_mock_page(blocks, page_height=792):
    """Helper to create a mock page with correct rect and get_text."""
    mock_page = MagicMock()
    mock_page.rect.height = page_height
    mock_page.get_text.return_value = {"blocks": blocks}
    return mock_page


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_normalizes_unicode_dashes(self):
        assert clean_text("word\u2013word") == "word-word"
        assert clean_text("word\u2014word") == "word--word"

    def test_normalizes_unicode_quotes(self):
        assert clean_text("\u201cquoted\u201d") == '"quoted"'
        assert clean_text("\u2018single\u2019") == "'single'"

    def test_normalizes_whitespace(self):
        assert clean_text("word   word") == "word word"
        assert clean_text("word\n\nword") == "word word"

    def test_removes_artifacts(self):
        assert clean_text("\u00c2word") == "word"


# ---------------------------------------------------------------------------
# fix_text_artifacts
# ---------------------------------------------------------------------------

class TestFixTextArtifacts:
    def test_fixes_merged_of_year(self):
        assert fix_text_artifacts("of2016") == "of 2016"
        assert fix_text_artifacts("2016of") == "2016 of"

    def test_fixes_ocr_errors(self):
        assert fix_text_artifacts("n'o way") == "no way"
        assert fix_text_artifacts("end.~start") == "end.start"

    def test_removes_margin_markers(self):
        assert "A" not in fix_text_artifacts("text A more")
        assert "B" not in fix_text_artifacts("text B more")

    def test_fixes_possessive_apostrophes(self):
        assert fix_text_artifacts("respondent's") == "respondent's"
        assert fix_text_artifacts("court's") == "court's"

    def test_removes_trailing_page_numbers(self):
        assert fix_text_artifacts("end of text 133") == "end of text"

    def test_fixes_hyphenation_breaks(self):
        assert fix_text_artifacts("judg- ment") == "judgment"


# ---------------------------------------------------------------------------
# is_header_footer
# ---------------------------------------------------------------------------

class TestIsHeaderFooter:
    def test_filters_page_numbers(self):
        assert is_header_footer("123", (0, 0, 50, 20), 792)
        assert is_header_footer("45", (0, 0, 50, 20), 792)

    def test_filters_scr_header(self):
        assert is_header_footer("Supreme Court Reports 2023", (0, 50, 400, 70), 792)

    def test_filters_top_margin_text(self):
        assert is_header_footer("Short header", (0, 10, 400, 30), 792)

    def test_filters_bottom_margin_text(self):
        assert is_header_footer("Footer text", (0, 750, 400, 780), 792)

    def test_allows_main_content(self):
        assert not is_header_footer("Main paragraph content", (0, 300, 400, 320), 792)


# ---------------------------------------------------------------------------
# is_margin_junk
# ---------------------------------------------------------------------------

class TestIsMarginJunk:
    def test_filters_single_letter_in_marker_column(self):
        marker_columns = [25]
        assert is_margin_junk("A", (20, 100, 30, 120), marker_columns)

    def test_filters_single_letter_in_left_margin(self):
        assert is_margin_junk("B", (10, 100, 40, 120), [])

    def test_filters_single_letter_in_right_margin(self):
        assert is_margin_junk("C", (450, 100, 480, 120), [])

    def test_allows_multi_char_text(self):
        assert not is_margin_junk("ABC", (10, 100, 40, 120), [])

    def test_allows_single_letter_in_content_area(self):
        assert not is_margin_junk("I", (200, 100, 220, 120), [])


# ---------------------------------------------------------------------------
# is_valid_heading
# ---------------------------------------------------------------------------

class TestIsValidHeading:
    def test_recognizes_judgment_heading(self):
        assert is_valid_heading("JUDGMENT", True, (60, 200, 400, 220), 792)

    def test_recognizes_order_heading(self):
        assert is_valid_heading("ORDER", True, (60, 200, 400, 220), 792)

    def test_recognizes_headnote(self):
        assert is_valid_heading("Headnote", True, (60, 200, 400, 220), 792)

    def test_rejects_judge_names(self):
        assert not is_valid_heading("[SMITH AND JONES, JJ.]", True, (60, 200, 400, 220), 792)

    def test_rejects_case_numbers(self):
        assert not is_valid_heading("(Civil Appeal No. 123)", True, (60, 200, 400, 220), 792)

    def test_rejects_non_bold(self):
        assert not is_valid_heading("JUDGMENT", False, (60, 200, 400, 220), 792)

    def test_rejects_too_short(self):
        assert not is_valid_heading("AB", True, (60, 200, 400, 220), 792)

    def test_rejects_too_long(self):
        long_text = "A" * 101
        assert not is_valid_heading(long_text, True, (60, 200, 400, 220), 792)

    def test_rejects_header_zone(self):
        assert not is_valid_heading("JUDGMENT", True, (60, 50, 400, 70), 792)

    def test_rejects_party_names_with_v(self):
        assert not is_valid_heading("SMITH V. JONES", True, (60, 200, 400, 220), 792)


# ---------------------------------------------------------------------------
# should_merge_blocks
# ---------------------------------------------------------------------------

class TestShouldMergeBlocks:
    def test_merges_incomplete_sentence(self):
        assert should_merge_blocks("The court held that", "the appellant", False)

    def test_merges_when_current_starts_lowercase(self):
        assert should_merge_blocks("End of sentence.", "continued here", True)

    def test_no_merge_after_complete_sentence(self):
        assert not should_merge_blocks("Complete sentence.", "New sentence", False)

    def test_no_merge_empty_previous(self):
        assert not should_merge_blocks("", "text", False)


# ---------------------------------------------------------------------------
# merge_line_spans
# ---------------------------------------------------------------------------

class TestMergeLineSpans:
    def test_merges_adjacent_same_format(self):
        spans = [
            {"text": "word1", "is_bold": False},
            {"text": "word2", "is_bold": False},
            {"text": "word3", "is_bold": False},
        ]
        merged = merge_line_spans(spans)
        assert len(merged) == 1
        assert merged[0]["text"] == "word1 word2 word3"

    def test_preserves_format_changes(self):
        spans = [
            {"text": "normal", "is_bold": False},
            {"text": "bold", "is_bold": True},
            {"text": "normal", "is_bold": False},
        ]
        merged = merge_line_spans(spans)
        assert len(merged) == 3

    def test_handles_empty_input(self):
        assert merge_line_spans([]) == []


# ---------------------------------------------------------------------------
# merge_paragraphs
# ---------------------------------------------------------------------------

class TestMergeParagraphs:
    def test_separates_headings(self):
        blocks = [
            {"text": "JUDGMENT", "spans": [], "is_heading": True},
            {"text": "The court finds.", "spans": [], "is_heading": False},
        ]
        merged = merge_paragraphs(blocks)
        assert len(merged) == 2
        assert merged[0]["type"] == "heading"
        assert merged[1]["type"] == "paragraph"

    def test_merges_continuous_text(self):
        blocks = [
            {"text": "First part of", "spans": [[{"text": "First part of", "is_bold": False}]], "is_heading": False},
            {"text": "the sentence.", "spans": [[{"text": "the sentence.", "is_bold": False}]], "is_heading": False},
        ]
        merged = merge_paragraphs(blocks)
        assert len(merged) == 1
        assert "First part of" in merged[0]["text"]
        assert "the sentence" in merged[0]["text"]


# ---------------------------------------------------------------------------
# build_html
# ---------------------------------------------------------------------------

class TestBuildHtml:
    def test_creates_valid_html_structure(self):
        elements = [
            {"type": "heading", "text": "JUDGMENT"},
            {"type": "paragraph", "text": "Content", "spans": [[{"text": "Content", "is_bold": False}]]},
        ]
        html = build_html(elements)
        assert "<html>" in html
        assert "<h2>" in html
        assert "JUDGMENT" in html
        assert "<p>" in html

    def test_includes_bold_tags(self):
        elements = [
            {"type": "paragraph", "text": "Bold text", "spans": [[{"text": "Bold text", "is_bold": True}]]},
        ]
        html = build_html(elements)
        assert "<strong>" in html


# ---------------------------------------------------------------------------
# convert_pdf_to_html
# ---------------------------------------------------------------------------

class TestConvertPdfToHtml:
    def test_returns_string(self):
        with patch('pipelines.convert_pdf.fitz') as mock_fitz:
            mock_doc = MagicMock()
            mock_page = _make_mock_page([_make_block("Test content.")])
            mock_doc.__iter__ = lambda self: iter([mock_page])
            mock_doc.__len__ = lambda self: 1
            mock_fitz.open.return_value = mock_doc

            result = convert_pdf_to_html(Path("test.pdf"))

            assert isinstance(result, str)
            assert len(result) > 0
            assert "Test content" in result

    def test_handles_error(self):
        with patch('pipelines.convert_pdf.fitz') as mock_fitz:
            mock_fitz.open.side_effect = Exception("File not found")
            result = convert_pdf_to_html(Path("nonexistent.pdf"))
            assert result == ""

    def test_filters_headers_footers(self):
        with patch('pipelines.convert_pdf.fitz') as mock_fitz:
            mock_doc = MagicMock()
            mock_page = _make_mock_page([
                _make_block("Supreme Court Reports", bbox=(60, 10, 400, 30)),
                _make_block("Actual content.", bbox=(60, 300, 400, 320)),
                _make_block("123", bbox=(280, 750, 310, 770)),
            ])
            mock_doc.__iter__ = lambda self: iter([mock_page])
            mock_doc.__len__ = lambda self: 1
            mock_fitz.open.return_value = mock_doc

            result = convert_pdf_to_html(Path("test.pdf"))
            assert "Actual content" in result

    def test_heading_detection(self):
        with patch('pipelines.convert_pdf.fitz') as mock_fitz:
            mock_doc = MagicMock()
            mock_page = _make_mock_page([
                _make_block("JUDGMENT", bbox=(60, 200, 400, 220), bold=True),
                _make_block("The court finds...", bbox=(60, 250, 400, 270)),
            ])
            mock_doc.__iter__ = lambda self: iter([mock_page])
            mock_doc.__len__ = lambda self: 1
            mock_fitz.open.return_value = mock_doc

            result = convert_pdf_to_html(Path("test.pdf"))
            assert "<h2>" in result
            assert "JUDGMENT" in result

    def test_bold_preservation(self):
        with patch('pipelines.convert_pdf.fitz') as mock_fitz:
            mock_doc = MagicMock()
            mock_page = _make_mock_page([
                {
                    "type": 0,
                    "bbox": (60, 300, 400, 320),
                    "lines": [
                        {
                            "spans": [
                                {"text": "Normal ", "flags": 0, "font": "Regular"},
                                {"text": "bold", "flags": 16, "font": "Bold"},
                            ]
                        }
                    ]
                }
            ])
            mock_doc.__iter__ = lambda self: iter([mock_page])
            mock_doc.__len__ = lambda self: 1
            mock_fitz.open.return_value = mock_doc

            result = convert_pdf_to_html(Path("test.pdf"))
            assert "<strong>" in result


# ---------------------------------------------------------------------------
# convert_bulk_htmls
# ---------------------------------------------------------------------------

class TestConvertBulkHtmls:
    def test_creates_output_directory(self, tmp_path):
        source_dir = tmp_path / "source"
        output_dir = tmp_path / "output"
        source_dir.mkdir()

        (source_dir / "test.pdf").touch()

        with patch('pipelines.convert_pdf.convert_pdf_to_html') as mock_convert:
            mock_convert.return_value = "<p>Test</p>"
            convert_bulk_htmls(str(source_dir), str(output_dir))

        assert output_dir.exists()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_parses_args(self, tmp_path):
        source_dir = tmp_path / "source"
        output_dir = tmp_path / "output"
        source_dir.mkdir()

        with patch('sys.argv', ['convert_pdf', str(source_dir), str(output_dir)]):
            with patch('pipelines.convert_pdf.convert_bulk_htmls') as mock_convert:
                main()
                mock_convert.assert_called_once()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_heading_keywords_nonempty(self):
        assert len(HEADING_KEYWORDS) > 0
        assert "judgment" in HEADING_KEYWORDS
        assert "order" in HEADING_KEYWORDS

    def test_non_heading_patterns_compile(self):
        for pattern in NON_HEADING_PATTERNS:
            assert pattern.pattern
