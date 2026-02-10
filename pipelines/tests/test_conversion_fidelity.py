"""Tests for conversion_fidelity module."""

from pipelines.conversion_fidelity import (
    ARTIFACT_PATTERNS,
    FALSE_HEADING_PATTERNS,
    FRAGMENTATION_PATTERNS,
    VALID_HEADING_KEYWORDS,
    FidelityReport,
    score_conversion,
    score_conversion_from_file,
)

# ---------------------------------------------------------------------------
# FidelityReport
# ---------------------------------------------------------------------------


class TestFidelityReport:
    def test_overall_score_weighted_average(self):
        report = FidelityReport(
            text_continuity_score=100,
            heading_accuracy_score=100,
            artifact_score=100,
            structure_score=100,
        )
        assert report.overall_score == 100.0

    def test_overall_score_partial(self):
        report = FidelityReport(
            text_continuity_score=50,
            heading_accuracy_score=50,
            artifact_score=50,
            structure_score=50,
        )
        assert report.overall_score == 50.0

    def test_passed_threshold(self):
        passing = FidelityReport(
            text_continuity_score=70,
            heading_accuracy_score=70,
            artifact_score=70,
            structure_score=70,
        )
        assert passing.passed

        failing = FidelityReport(
            text_continuity_score=50,
            heading_accuracy_score=50,
            artifact_score=50,
            structure_score=50,
        )
        assert not failing.passed

    def test_default_values(self):
        report = FidelityReport()
        assert report.overall_score == 100.0
        assert report.fragmented_lines == 0
        assert report.issues == []


# ---------------------------------------------------------------------------
# score_conversion
# ---------------------------------------------------------------------------


class TestScoreConversion:
    def test_empty_html(self):
        report = score_conversion("")
        assert report.text_continuity_score == 0
        assert "No body element" in report.issues[0]

    def test_minimal_valid_html(self):
        html = """<html><body>
        <p>This is a reasonably long paragraph with enough words to pass the
        structure scoring thresholds that check for proper paragraph length.</p>
        </body></html>"""
        report = score_conversion(html)
        assert report.passed

    def test_detects_false_heading_judge_names(self):
        html = """
        <html><body>
        <h2>[SMITH AND JONES, JJ.]</h2>
        <p>Some content.</p>
        </body></html>
        """
        report = score_conversion(html)
        assert report.false_headings >= 1
        assert report.heading_accuracy_score < 100

    def test_detects_false_heading_dates(self):
        html = """
        <html><body>
        <h2>JANUARY 15, 2023</h2>
        <p>Some content.</p>
        </body></html>
        """
        report = score_conversion(html)
        assert report.false_headings >= 1

    def test_valid_heading_keywords_not_flagged(self):
        html = """
        <html><body>
        <h2>JUDGMENT</h2>
        <p>The court finds...</p>
        </body></html>
        """
        report = score_conversion(html)
        assert report.false_headings == 0
        assert report.heading_accuracy_score == 100

    def test_detects_merged_year_artifact(self):
        html = "<html><body><p>Appeal of2016 was filed.</p></body></html>"
        report = score_conversion(html)
        assert report.fragmented_lines >= 1

    def test_detects_ocr_artifacts(self):
        html = "<html><body><p>The court's decision was clear.</p></body></html>"
        report = score_conversion(html)
        # Curly apostrophe is an artifact pattern
        assert report.artifacts_found >= 1

    def test_high_score_for_clean_document(self):
        html = """
        <html><body>
        <h2>JUDGMENT</h2>
        <p>This is a well-formatted paragraph with proper punctuation and flow.
        The text continues naturally without any artifacts or issues.</p>
        <p>Another paragraph follows with similar quality content that demonstrates
        good conversion from the source document.</p>
        </body></html>
        """
        report = score_conversion(html)
        assert report.overall_score >= 80

    def test_short_paragraphs_reduce_structure_score(self):
        html = """
        <html><body>
        <p>One.</p>
        <p>Two.</p>
        <p>Three.</p>
        <p>Four.</p>
        <p>Five.</p>
        </body></html>
        """
        report = score_conversion(html)
        assert report.structure_score < 100


# ---------------------------------------------------------------------------
# score_conversion_from_file
# ---------------------------------------------------------------------------


class TestScoreConversionFromFile:
    def test_reads_and_scores_file(self, tmp_path):
        html_file = tmp_path / "test.html"
        html_file.write_text(
            """<html><body>
            <p>This is a properly formatted paragraph with sufficient length
            to pass all the scoring thresholds for document structure.</p>
            </body></html>""",
            encoding="utf-8",
        )
        report = score_conversion_from_file(html_file)
        assert report.passed


# ---------------------------------------------------------------------------
# Pattern coverage
# ---------------------------------------------------------------------------


class TestPatternCoverage:
    def test_fragmentation_patterns_compile(self):
        for pattern in FRAGMENTATION_PATTERNS:
            assert pattern.pattern  # Ensure each is a compiled regex

    def test_artifact_patterns_compile(self):
        for pattern in ARTIFACT_PATTERNS:
            assert pattern.pattern

    def test_false_heading_patterns_compile(self):
        for pattern in FALSE_HEADING_PATTERNS:
            assert pattern.pattern

    def test_heading_keywords_nonempty(self):
        assert len(VALID_HEADING_KEYWORDS) > 0
        assert "judgment" in VALID_HEADING_KEYWORDS


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_only_headings_no_paragraphs(self):
        html = "<html><body><h2>HEADING</h2></body></html>"
        report = score_conversion(html)
        assert report.structure_score < 100

    def test_unicode_content(self):
        html = (
            "<html><body><p>Legal text with \u2014 dashes and \u201cquotes\u201d.</p></body></html>"
        )
        report = score_conversion(html)
        assert report is not None

    def test_nested_structure(self):
        html = """
        <html><body>
        <div>
            <p>This is a nested paragraph with enough content to pass the
            structure scoring that checks for adequate paragraph length.</p>
        </div>
        </body></html>
        """
        report = score_conversion(html)
        assert report.passed

    def test_multiple_heading_levels(self):
        html = """
        <html><body>
        <h1>Main Title</h1>
        <h2>JUDGMENT</h2>
        <h3>Section A</h3>
        <p>Content here.</p>
        </body></html>
        """
        report = score_conversion(html)
        assert report is not None
