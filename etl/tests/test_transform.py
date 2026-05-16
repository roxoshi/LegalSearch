import html as _html
from unittest.mock import Mock, patch

import numpy as np
import pytest

from etl.schemas import ANALYSIS_FIELD_LABELS, AnalysisJSON, MetadataJSON
from etl.transform import Transformer, generate_html


# ─── generate_html ────────────────────────────────────────────────────────────


def test_generate_html_contains_all_headings(sample_analysis_data):
    analysis = AnalysisJSON(**sample_analysis_data)
    output = generate_html(analysis)
    for heading in ANALYSIS_FIELD_LABELS.values():
        # Headings are HTML-escaped in the output (e.g. apostrophes → &#x27;)
        assert _html.escape(heading) in output, f"Heading '{heading}' not found in HTML"


def test_generate_html_wraps_in_case_analysis_div(sample_analysis_data):
    analysis = AnalysisJSON(**sample_analysis_data)
    html = generate_html(analysis)
    assert html.startswith('<div class="case-analysis">')
    assert html.endswith("</div>")


def test_generate_html_sections_use_correct_tags(sample_analysis_data):
    analysis = AnalysisJSON(**sample_analysis_data)
    html = generate_html(analysis)
    assert '<section class="case-section">' in html
    assert "<h2>" in html
    assert "<p>" in html


def test_generate_html_escapes_html_content():
    """Angle brackets and ampersands in field text should be HTML-escaped."""
    data = {
        "summary": "<b>Bold</b> & 'quoted'",
        "facts": "normal",
        "issues": "normal",
        "petitioner_arguments": "normal",
        "respondent_arguments": "normal",
        "analysis_of_law": "normal",
        "precedent_analysis": "normal",
        "courts_reasoning": "normal",
        "conclusion": "normal",
        "ratio_decidendi": "normal",
    }
    analysis = AnalysisJSON(**data)
    html = generate_html(analysis)
    assert "&lt;b&gt;" in html
    assert "&amp;" in html
    assert "<b>" not in html


def test_generate_html_splits_double_newlines_into_paragraphs():
    data = {
        "summary": "First paragraph.\n\nSecond paragraph.",
        "facts": "f",
        "issues": "i",
        "petitioner_arguments": "p",
        "respondent_arguments": "r",
        "analysis_of_law": "a",
        "precedent_analysis": "pr",
        "courts_reasoning": "c",
        "conclusion": "con",
        "ratio_decidendi": "rd",
    }
    analysis = AnalysisJSON(**data)
    html = generate_html(analysis)
    assert html.count("<p>") >= 2


# ─── Transformer ─────────────────────────────────────────────────────────────


@pytest.fixture
def transformer():
    """Transformer with a mocked embedding model (no GPU/model load needed)."""
    with patch("etl.transform.EmbeddingModel") as mock_cls:
        mock_model = Mock()
        # Return 2-D numpy array: one 384-dim vector per input text
        mock_model.encode.side_effect = lambda texts: np.array([[0.1] * 384] * len(texts))
        mock_cls.return_value = mock_model
        t = Transformer(model_name="test-model")
        yield t


def test_process_document_creates_up_to_ten_chunks(
    transformer, sample_analysis_data, sample_metadata_data
):
    analysis = AnalysisJSON(**sample_analysis_data)
    metadata = MetadataJSON(**sample_metadata_data)
    _, chunks = transformer.process_document("TEST-2024-001", analysis, metadata)
    assert len(chunks) <= 10
    assert len(chunks) > 0


def test_process_document_skips_not_mentioned_fields(transformer, sample_analysis_data):
    """Fields with 'Not Mentioned' should not produce chunks."""
    data = {**sample_analysis_data, "precedent_analysis": "Not Mentioned"}
    analysis = AnalysisJSON(**data)
    metadata = MetadataJSON()
    _, chunks = transformer.process_document("CASE-001", analysis, metadata)
    # precedent_analysis chunk should be absent
    chunk_texts = [c.chunk_content for c in chunks]
    assert "Not Mentioned" not in chunk_texts


def test_process_document_chunk_content_matches_field(
    transformer, sample_analysis_data, sample_metadata_data
):
    analysis = AnalysisJSON(**sample_analysis_data)
    metadata = MetadataJSON(**sample_metadata_data)
    _, chunks = transformer.process_document("TEST-001", analysis, metadata)
    chunk_texts = {c.chunk_content for c in chunks}
    # Each non-empty field value should appear as a chunk
    assert sample_analysis_data["summary"] in chunk_texts
    assert sample_analysis_data["conclusion"] in chunk_texts


def test_process_document_html_in_display_content(
    transformer, sample_analysis_data, sample_metadata_data
):
    analysis = AnalysisJSON(**sample_analysis_data)
    metadata = MetadataJSON(**sample_metadata_data)
    doc, _ = transformer.process_document("TEST-001", analysis, metadata)
    assert doc.display_content is not None
    assert "<div" in doc.display_content
    assert "<section" in doc.display_content


def test_process_document_metadata_defaults_use_case_id(transformer, sample_analysis_data):
    """When metadata has no title, document title should fall back to case_id."""
    analysis = AnalysisJSON(**sample_analysis_data)
    metadata = MetadataJSON()  # title=""
    doc, _ = transformer.process_document("FALLBACK-CASE-ID", analysis, metadata)
    assert doc.title == "FALLBACK-CASE-ID"


def test_process_document_content_starts_with_summary(
    transformer, sample_analysis_data, sample_metadata_data
):
    analysis = AnalysisJSON(**sample_analysis_data)
    metadata = MetadataJSON(**sample_metadata_data)
    doc, _ = transformer.process_document("TEST-001", analysis, metadata)
    assert doc.content.startswith(sample_analysis_data["summary"])


def test_process_document_chunk_embeddings_have_correct_dimension(
    transformer, sample_analysis_data, sample_metadata_data
):
    analysis = AnalysisJSON(**sample_analysis_data)
    metadata = MetadataJSON(**sample_metadata_data)
    _, chunks = transformer.process_document("TEST-001", analysis, metadata)
    for chunk in chunks:
        assert chunk.embedding is not None
        assert len(chunk.embedding) == 384


def test_transformer_initialization():
    with patch("etl.transform.EmbeddingModel") as mock_cls:
        Transformer(model_name="test-model")
        mock_cls.assert_called_once_with("test-model")
