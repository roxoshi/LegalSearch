"""
Comprehensive tests for pipeline optimizations.

Tests cover:
1. Keyword pre-filter functionality
2. PyMuPDF text extraction
3. ONNX embeddings (with fallback)
4. Two-stage filtering
"""

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipelines.optimizations import (
    ONNXEmbedder,
    extract_text_pdfplumber,
    extract_text_pymupdf,
    keyword_prefilter,
    two_stage_filter,
)

# ============================================================================
# Test Fixtures
# ============================================================================


@dataclass
class MockDocument:
    """Mock document for testing."""

    case_id: str
    text_content: str = ""
    extracted_provisions: list[str] = field(default_factory=list)
    extracted_statutes: list[str] = field(default_factory=list)
    is_gst_core: bool | None = None


@pytest.fixture
def gst_document_text():
    """Sample GST-related document text."""
    return """
    IN THE SUPREME COURT OF INDIA
    CIVIL APPEAL NO. 1234 OF 2023

    The appellant challenges the order passed under Section 73 of the CGST Act, 2017.
    The dispute pertains to the availment of Input Tax Credit (ITC) under the GST regime.
    The Central Goods and Services Tax Act provides for levy and collection of tax on
    intra-state supply of goods and services.

    The respondent contends that the IGST payable on imported goods was not properly
    accounted for under the reverse charge mechanism.

    JUDGMENT:
    After considering the provisions of the GST Act and the CGST Rules, we find that
    the appeal has merit. The Input Tax Credit claimed by the appellant is valid.
    """


@pytest.fixture
def non_gst_document_text():
    """Sample non-GST document text."""
    return """
    IN THE SUPREME COURT OF INDIA
    CRIMINAL APPEAL NO. 5678 OF 2023

    The appellant has been convicted under Section 302 of the Indian Penal Code
    for the offence of murder. The trial court sentenced the appellant to life imprisonment.

    The prosecution case is that on the fateful night, the appellant attacked the
    deceased with a sharp weapon causing multiple injuries leading to death.

    The High Court has confirmed the conviction and sentence. The appellant is now
    before this Court challenging the same.

    JUDGMENT:
    After careful consideration of the evidence on record, we find no merit in this appeal.
    The conviction is upheld.
    """


@pytest.fixture
def sample_pdf_bytes():
    """Create a minimal valid PDF for testing."""
    # This is a minimal valid PDF structure
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 100 700 Td (GST Tax Document) Tj ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
306
%%EOF"""
    return pdf_content


# ============================================================================
# Tests for keyword_prefilter
# ============================================================================


class TestKeywordPrefilter:
    """Tests for the keyword pre-filter function."""

    def test_empty_text_returns_false(self):
        """Empty text should not match."""
        is_gst, matched = keyword_prefilter("")
        assert is_gst is False
        assert len(matched) == 0

    def test_none_text_returns_false(self):
        """None text should not match."""
        is_gst, matched = keyword_prefilter(None)
        assert is_gst is False
        assert len(matched) == 0

    def test_gst_keyword_matches(self):
        """Direct GST keyword should match."""
        is_gst, matched = keyword_prefilter("This case involves GST provisions")
        assert is_gst is True
        assert "gst" in matched

    def test_cgst_keyword_matches(self):
        """CGST keyword should match."""
        is_gst, matched = keyword_prefilter("The CGST Act applies here")
        assert is_gst is True
        assert "cgst" in matched

    def test_sgst_keyword_matches(self):
        """SGST keyword should match."""
        is_gst, matched = keyword_prefilter("SGST was not paid properly")
        assert is_gst is True
        assert "sgst" in matched

    def test_igst_keyword_matches(self):
        """IGST keyword should match."""
        is_gst, matched = keyword_prefilter("IGST on imports is disputed")
        assert is_gst is True
        assert "igst" in matched

    def test_goods_and_services_tax_matches(self):
        """Full phrase 'Goods and Services Tax' should match."""
        is_gst, matched = keyword_prefilter("The Goods and Services Tax Act, 2017")
        assert is_gst is True
        assert "goods and services tax" in matched

    def test_input_tax_credit_matches(self):
        """ITC keyword should match."""
        is_gst, matched = keyword_prefilter("Input Tax Credit was disallowed")
        assert is_gst is True
        assert "input tax credit" in matched

    def test_itc_abbreviation_matches(self):
        """ITC abbreviation should match."""
        is_gst, matched = keyword_prefilter("The ITC claim was rejected")
        assert is_gst is True
        assert "itc" in matched

    def test_case_insensitive_matching(self):
        """Keywords should match regardless of case."""
        texts = ["gst", "GST", "Gst", "gSt"]
        for text in texts:
            is_gst, matched = keyword_prefilter(f"About {text} law")
            assert is_gst is True, f"Failed for: {text}"

    def test_pattern_with_dots_matches(self):
        """G.S.T. with dots should match."""
        is_gst, matched = keyword_prefilter("The G.S.T. provisions apply")
        assert is_gst is True

    def test_non_gst_text_does_not_match(self, non_gst_document_text):
        """Non-GST document should not match."""
        is_gst, matched = keyword_prefilter(non_gst_document_text)
        assert is_gst is False
        assert len(matched) == 0

    def test_gst_document_matches(self, gst_document_text):
        """GST document should match with multiple keywords."""
        is_gst, matched = keyword_prefilter(gst_document_text)
        assert is_gst is True
        assert len(matched) > 0
        # Should find multiple GST-related terms
        assert "cgst" in matched or "gst" in matched

    def test_e_way_bill_matches(self):
        """E-way bill keyword should match."""
        is_gst, matched = keyword_prefilter("E-way bill was not generated")
        assert is_gst is True
        assert "e-way bill" in matched

    def test_hsn_code_matches(self):
        """HSN code keyword should match."""
        is_gst, matched = keyword_prefilter("Wrong HSN code was applied")
        assert is_gst is True
        assert "hsn code" in matched

    def test_reverse_charge_matches(self):
        """Reverse charge keyword should match."""
        is_gst, matched = keyword_prefilter("Reverse charge mechanism applies")
        assert is_gst is True
        assert "reverse charge" in matched

    def test_multiple_keywords_all_captured(self):
        """Multiple keywords in text should all be captured."""
        text = "GST and CGST and IGST are all applicable"
        is_gst, matched = keyword_prefilter(text)
        assert is_gst is True
        assert "gst" in matched
        assert "cgst" in matched
        assert "igst" in matched

    def test_section_reference_pattern_matches(self):
        """Section references to GST acts should match."""
        text = "Section 16 of the CGST Act provides for input tax credit"
        is_gst, matched = keyword_prefilter(text)
        assert is_gst is True

    def test_composition_scheme_matches(self):
        """Composition scheme keyword should match."""
        is_gst, matched = keyword_prefilter("The dealer opted for composition scheme")
        assert is_gst is True
        assert "composition scheme" in matched

    def test_wbgst_matches(self):
        """State GST compound form WBGST should match without enumerating state codes."""
        is_gst, matched = keyword_prefilter("The WBGST Act, 2017 applies to this transaction")
        assert is_gst is True
        assert "wbgst" in matched

    def test_gangster_does_not_match(self):
        """'gangster' contains 'gst' as substring but should not match (lowercase context)."""
        is_gst, matched = keyword_prefilter("The gangster was convicted under IPC")
        assert is_gst is False
        assert len(matched) == 0

    def test_other_state_gst_variants_match(self):
        """Other state GST compound forms should match without enumeration."""
        cases = [
            ("KGST provisions apply", "kgst"),
            ("Under the UPGST Act", "upgst"),
            ("TNGST registration required", "tngst"),
        ]
        for text, expected_token in cases:
            is_gst, matched = keyword_prefilter(text)
            assert is_gst is True, f"Failed for: {text}"
            assert expected_token in matched, f"Expected '{expected_token}' in {matched}"


# ============================================================================
# Tests for PyMuPDF text extraction
# ============================================================================


class TestPyMuPDFExtraction:
    """Tests for PyMuPDF-based text extraction."""

    def test_empty_bytes_returns_empty(self):
        """Empty bytes should return empty string."""
        result = extract_text_pymupdf(b"")
        assert result == ""

    def test_invalid_pdf_returns_empty(self):
        """Invalid PDF bytes should return empty string gracefully."""
        result = extract_text_pymupdf(b"not a pdf")
        assert result == ""

    def test_valid_pdf_extracts_text(self, sample_pdf_bytes):
        """Valid PDF should extract text."""
        # Note: minimal PDF may not render text properly,
        # but function should not crash
        result = extract_text_pymupdf(sample_pdf_bytes)
        # Should return string (may be empty for minimal PDF)
        assert isinstance(result, str)

    def test_max_chars_limit_respected(self):
        """Max chars limit should be respected."""
        # Create a mock that would return long text
        with patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_page = MagicMock()
            mock_page.get_text.return_value = "A" * 10000
            mock_doc.__iter__ = lambda self: iter([mock_page])
            mock_doc.close = MagicMock()
            mock_fitz.return_value = mock_doc

            result = extract_text_pymupdf(b"fake pdf", max_chars=100)
            assert len(result) <= 100

    def test_multiple_pages_concatenated(self):
        """Text from multiple pages should be concatenated."""
        with patch("fitz.open") as mock_fitz:
            mock_doc = MagicMock()
            mock_page1 = MagicMock()
            mock_page1.get_text.return_value = "Page 1 content"
            mock_page2 = MagicMock()
            mock_page2.get_text.return_value = "Page 2 content"
            mock_doc.__iter__ = lambda self: iter([mock_page1, mock_page2])
            mock_doc.close = MagicMock()
            mock_fitz.return_value = mock_doc

            result = extract_text_pymupdf(b"fake pdf", max_chars=10000)
            assert "Page 1 content" in result
            assert "Page 2 content" in result


# ============================================================================
# Tests for pdfplumber text extraction (for comparison)
# ============================================================================


class TestPdfplumberExtraction:
    """Tests for pdfplumber-based text extraction."""

    def test_empty_bytes_returns_empty(self):
        """Empty bytes should return empty string."""
        result = extract_text_pdfplumber(b"")
        assert result == ""

    def test_invalid_pdf_returns_empty(self):
        """Invalid PDF bytes should return empty string gracefully."""
        result = extract_text_pdfplumber(b"not a pdf")
        assert result == ""

    def test_valid_pdf_extracts_text_with_mock(self):
        """Valid PDF should extract text using pdfplumber."""
        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "Test content from pdfplumber"
            mock_pdf.pages = [mock_page]
            mock_pdf.__enter__ = lambda self: mock_pdf
            mock_pdf.__exit__ = lambda self, *args: None
            mock_open.return_value = mock_pdf

            result = extract_text_pdfplumber(b"fake pdf")
            assert "Test content from pdfplumber" in result

    def test_max_chars_limit_with_mock(self):
        """Max chars limit should be respected."""
        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "A" * 10000
            mock_pdf.pages = [mock_page]
            mock_pdf.__enter__ = lambda self: mock_pdf
            mock_pdf.__exit__ = lambda self, *args: None
            mock_open.return_value = mock_pdf

            result = extract_text_pdfplumber(b"fake pdf", max_chars=100)
            assert len(result) <= 100

    def test_multiple_pages_with_mock(self):
        """Multiple pages should be concatenated."""
        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_page1 = MagicMock()
            mock_page1.extract_text.return_value = "Page 1"
            mock_page2 = MagicMock()
            mock_page2.extract_text.return_value = "Page 2"
            mock_pdf.pages = [mock_page1, mock_page2]
            mock_pdf.__enter__ = lambda self: mock_pdf
            mock_pdf.__exit__ = lambda self, *args: None
            mock_open.return_value = mock_pdf

            result = extract_text_pdfplumber(b"fake pdf", max_chars=10000)
            assert "Page 1" in result
            assert "Page 2" in result

    def test_empty_pages_handled(self):
        """Empty pages should be handled gracefully."""
        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_page1 = MagicMock()
            mock_page1.extract_text.return_value = None  # Empty page
            mock_page2 = MagicMock()
            mock_page2.extract_text.return_value = "Content"
            mock_pdf.pages = [mock_page1, mock_page2]
            mock_pdf.__enter__ = lambda self: mock_pdf
            mock_pdf.__exit__ = lambda self, *args: None
            mock_open.return_value = mock_pdf

            result = extract_text_pdfplumber(b"fake pdf")
            assert "Content" in result


# ============================================================================
# Tests for ONNXEmbedder
# ============================================================================


class TestONNXEmbedder:
    """Tests for ONNX-based embeddings."""

    def test_initialization(self):
        """Embedder should initialize without errors."""
        embedder = ONNXEmbedder()
        assert embedder.model_name == "sentence-transformers/all-MiniLM-L6-v2"
        assert embedder._initialized is False

    def test_custom_model_name(self):
        """Custom model name should be stored."""
        embedder = ONNXEmbedder(model_name="custom/model")
        assert embedder.model_name == "custom/model"

    def test_encode_returns_list(self):
        """Encode should return list of embeddings."""
        import numpy as np

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])

        embedder = ONNXEmbedder()
        # Force fallback path
        embedder._initialized = True
        embedder._use_onnx = False
        embedder.model = mock_model

        result = embedder.encode(["text 1", "text 2"])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_encode_empty_list(self):
        """Encode empty list should return empty list."""
        import numpy as np

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([])

        embedder = ONNXEmbedder()
        embedder._initialized = True
        embedder._use_onnx = False
        embedder.model = mock_model

        result = embedder.encode([])
        assert result == []

    def test_onnx_encode_path(self):
        """Test the ONNX encoding path with mocks."""
        import numpy as np

        embedder = ONNXEmbedder()
        embedder._initialized = True
        embedder._use_onnx = True

        # Mock tokenizer
        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {
            "input_ids": np.array([[1, 2, 3]]),
            "attention_mask": np.array([[1, 1, 1]]),
        }
        embedder.tokenizer = mock_tokenizer

        # Mock model with proper output structure
        mock_model = MagicMock()
        mock_output = MagicMock()
        mock_output.last_hidden_state = np.array([[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]])
        mock_model.return_value = mock_output
        embedder.model = mock_model

        result = embedder.encode(["test text"], batch_size=1)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], list)

    def test_onnx_encode_multiple_batches(self):
        """Test ONNX encoding with multiple batches."""
        import numpy as np

        embedder = ONNXEmbedder()
        embedder._initialized = True
        embedder._use_onnx = True

        # Mock tokenizer
        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {
            "input_ids": np.array([[1, 2, 3]]),
            "attention_mask": np.array([[1, 1, 1]]),
        }
        embedder.tokenizer = mock_tokenizer

        # Mock model
        mock_model = MagicMock()
        mock_output = MagicMock()
        mock_output.last_hidden_state = np.array([[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]])
        mock_model.return_value = mock_output
        embedder.model = mock_model

        # Encode 3 texts with batch size 1 (should create 3 batches)
        result = embedder.encode(["text1", "text2", "text3"], batch_size=1)

        assert len(result) == 3

    def test_ensure_initialized_fallback(self):
        """Test initialization fallback to EmbeddingModel."""
        with patch.dict("sys.modules", {"optimum": None, "optimum.onnxruntime": None}):
            with patch("app.embeddings.EmbeddingModel") as mock_st:
                import numpy as np

                mock_model = MagicMock()
                mock_model.encode.return_value = np.array([[0.1, 0.2]])
                mock_st.return_value = mock_model

                embedder = ONNXEmbedder()
                embedder._ensure_initialized()

                # Should have fallen back
                assert embedder._initialized is True


# ============================================================================
# Tests for two_stage_filter
# ============================================================================


class TestTwoStageFilter:
    """Tests for the two-stage filtering function."""

    @pytest.fixture
    def mock_nlp(self):
        """Create a mock spaCy NLP pipeline."""
        mock = MagicMock()

        def mock_pipe(texts, batch_size=32, n_process=1):
            """Mock spaCy pipe that returns docs with GST entities."""
            for text in texts:
                mock_doc = MagicMock()
                mock_doc.ents = []

                # Add GST statute entity if text contains GST
                if "gst" in text.lower():
                    mock_ent = MagicMock()
                    mock_ent.label_ = "STATUTE"
                    mock_ent.text = "Goods and Services Tax Act"
                    mock_doc.ents.append(mock_ent)

                yield mock_doc

        mock.pipe = mock_pipe
        return mock

    def test_empty_docs_returns_empty(self, mock_nlp):
        """Empty doc list should return empty."""
        result: list[Any]
        result, stats = two_stage_filter([], mock_nlp)
        assert result == []
        assert stats["total"] == 0

    def test_non_gst_docs_filtered_by_keyword(self, mock_nlp, non_gst_document_text):
        """Non-GST docs should be filtered at keyword stage."""
        docs = [MockDocument(case_id="1", text_content=non_gst_document_text)]

        result, stats = two_stage_filter(docs, mock_nlp)

        assert len(result) == 0
        assert stats["rejected_by_keyword"] == 1
        assert stats["passed_keyword_filter"] == 0

    def test_gst_docs_pass_keyword_filter(self, mock_nlp, gst_document_text):
        """GST docs should pass keyword filter."""
        docs = [MockDocument(case_id="1", text_content=gst_document_text)]

        result, stats = two_stage_filter(docs, mock_nlp)

        assert stats["passed_keyword_filter"] == 1

    def test_stats_contain_filter_rates(self, mock_nlp, gst_document_text, non_gst_document_text):
        """Stats should contain filter rates."""
        docs = [
            MockDocument(case_id="1", text_content=gst_document_text),
            MockDocument(case_id="2", text_content=non_gst_document_text),
        ]

        result, stats = two_stage_filter(docs, mock_nlp)

        assert "keyword_filter_rate" in stats
        assert "ner_filter_rate" in stats
        assert stats["total"] == 2
        assert stats["rejected_by_keyword"] == 1

    def test_custom_text_getter(self, mock_nlp):
        """Custom text getter should be used."""
        # Use MockDocument with custom text content attribute name
        docs = [MockDocument(case_id="1", text_content="This is about GST law")]

        # Custom getter that adds prefix (to prove it's being called)
        result, stats = two_stage_filter(
            docs,
            mock_nlp,
            text_getter=lambda d: d.text_content.upper(),  # Uppercase still matches GST
        )

        assert stats["passed_keyword_filter"] == 1

    def test_documents_get_ner_attributes(self, mock_nlp, gst_document_text):
        """Filtered documents should have NER attributes set."""
        docs = [MockDocument(case_id="1", text_content=gst_document_text)]

        result, stats = two_stage_filter(docs, mock_nlp)

        # The mock adds GST statute entities
        if result:
            assert hasattr(result[0], "extracted_statutes")
            assert hasattr(result[0], "extracted_provisions")
            assert hasattr(result[0], "is_gst_core")


# ============================================================================
# Integration tests
# ============================================================================


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_keyword_filter_reduces_ner_load(self):
        """Verify keyword filter significantly reduces NER workload."""
        # Create a mix of GST and non-GST documents
        gst_texts = [
            "GST Act provisions apply",
            "CGST and SGST both applicable",
            "Input Tax Credit claim",
        ]
        non_gst_texts = [
            "Criminal appeal dismissed",
            "Property dispute resolved",
            "Contract law principles",
            "Evidence Act provisions",
            "Motor vehicle accident case",
            "Family court jurisdiction",
            "Arbitration proceedings",
        ]

        gst_count = 0
        non_gst_count = 0

        for text in gst_texts:
            is_gst, _ = keyword_prefilter(text)
            if is_gst:
                gst_count += 1

        for text in non_gst_texts:
            is_gst, _ = keyword_prefilter(text)
            if is_gst:
                non_gst_count += 1

        # All GST texts should pass
        assert gst_count == len(gst_texts)

        # Most non-GST texts should be filtered
        assert non_gst_count < len(non_gst_texts) * 0.3  # <30% false positives

    def test_extraction_methods_comparable(self):
        """Both extraction methods should handle edge cases similarly."""
        # Both should handle empty input gracefully
        assert extract_text_pymupdf(b"") == extract_text_pdfplumber(b"")

        # Both should handle invalid PDF gracefully
        assert extract_text_pymupdf(b"invalid") == extract_text_pdfplumber(b"invalid")
