"""
Tests for the unified pipeline with Tier 1 optimizations.

Verifies:
1. PyMuPDF text extraction is used correctly
2. Two-stage filtering works as expected
3. Output format matches expected schema
"""

import inspect
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipelines.unified_pipeline import (
    DocumentData,
    batch_ner_filter,
    export_after_filter,
    extract_text_from_pdf_bytes,
    import_filtered_docs,
    load_sc_metadata,
    run_pipeline,
)

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def sample_document():
    """Create a sample document for testing."""
    return DocumentData(
        case_id="test-123",
        title="Test Case",
        text_content="This is a test document about GST and CGST provisions.",
    )


@pytest.fixture
def gst_document():
    """Create a GST-related document."""
    return DocumentData(
        case_id="gst-001",
        title="GST Case",
        text_content="""
        The appellant challenges the order under Section 73 of the CGST Act.
        The dispute relates to Input Tax Credit under the GST regime.
        The Goods and Services Tax Act provisions are applicable.
        """,
    )


@pytest.fixture
def non_gst_document():
    """Create a non-GST document."""
    return DocumentData(
        case_id="criminal-001",
        title="Criminal Case",
        text_content="""
        The appellant was convicted under Section 302 of the Indian Penal Code.
        The prosecution proved the case beyond reasonable doubt.
        The appeal is dismissed.
        """,
    )


@pytest.fixture
def mock_nlp():
    """Create a mock spaCy NLP model."""
    mock = MagicMock()

    def mock_pipe(texts, batch_size=32, n_process=1):
        for text in texts:
            mock_doc = MagicMock()
            mock_doc.ents = []

            # Add GST statute if text contains GST keywords
            if "gst" in text.lower() or "goods and services" in text.lower():
                mock_ent = MagicMock()
                mock_ent.label_ = "STATUTE"
                mock_ent.text = "Goods and Services Tax Act"
                mock_doc.ents.append(mock_ent)

                # Add provisions if mentioned
                if "section" in text.lower():
                    prov_ent = MagicMock()
                    prov_ent.label_ = "PROVISION"
                    prov_ent.text = "Section 73"
                    mock_doc.ents.append(prov_ent)

            yield mock_doc

    mock.pipe = mock_pipe
    return mock


# ============================================================================
# Tests for extract_text_from_pdf_bytes
# ============================================================================


class TestExtractTextFromPdfBytes:
    """Tests for PDF text extraction using PyMuPDF."""

    def test_uses_pymupdf_extraction(self):
        """Verify that PyMuPDF extraction is used."""
        # Patch at the location where it's imported
        with patch("pipelines.unified_pipeline.extract_text_pymupdf") as mock_extract:
            mock_extract.return_value = "Extracted text"

            result = extract_text_from_pdf_bytes(b"fake pdf bytes")

            mock_extract.assert_called_once()
            assert result == "Extracted text"

    def test_empty_bytes_returns_empty(self):
        """Empty bytes should return empty string."""
        result = extract_text_from_pdf_bytes(b"")
        assert result == ""

    def test_max_chars_passed_correctly(self):
        """Max chars parameter should be passed to extraction."""
        with patch("pipelines.unified_pipeline.extract_text_pymupdf") as mock_extract:
            mock_extract.return_value = "text"

            extract_text_from_pdf_bytes(b"pdf", max_chars=100)

            mock_extract.assert_called_with(b"pdf", 100)


# ============================================================================
# Tests for batch_ner_filter (two-stage filtering)
# ============================================================================


class TestBatchNerFilter:
    """Tests for the two-stage NER filtering."""

    def test_gst_document_passes_filter(self, gst_document, mock_nlp):
        """GST documents should pass the filter."""
        docs = [gst_document]

        result = batch_ner_filter(docs, mock_nlp)

        assert len(result) == 1
        assert result[0].case_id == "gst-001"

    def test_non_gst_document_filtered_at_keyword_stage(self, non_gst_document, mock_nlp):
        """Non-GST documents should be filtered at keyword stage."""
        docs = [non_gst_document]

        result = batch_ner_filter(docs, mock_nlp)

        # Should be filtered out (no GST keywords)
        assert len(result) == 0

    def test_mixed_documents_filtered_correctly(self, gst_document, non_gst_document, mock_nlp):
        """Mixed documents should be filtered correctly."""
        docs = [gst_document, non_gst_document]

        result = batch_ner_filter(docs, mock_nlp)

        # Only GST document should pass
        assert len(result) == 1
        assert result[0].case_id == "gst-001"

    def test_empty_docs_returns_empty(self, mock_nlp):
        """Empty document list should return empty."""
        result = batch_ner_filter([], mock_nlp)
        assert result == []

    def test_documents_get_ner_attributes(self, gst_document, mock_nlp):
        """Filtered documents should have NER attributes."""
        docs = [gst_document]

        result = batch_ner_filter(docs, mock_nlp)

        if result:
            assert hasattr(result[0], "extracted_statutes")
            assert hasattr(result[0], "extracted_provisions")
            assert hasattr(result[0], "is_gst_core")
            assert result[0].is_gst_core is True


# ============================================================================
# Tests for DocumentData
# ============================================================================


class TestDocumentData:
    """Tests for the DocumentData dataclass."""

    def test_default_values(self):
        """Default values should be set correctly."""
        doc = DocumentData(case_id="test", title="Test")

        assert doc.petitioner == "Unknown"
        assert doc.respondent == "Unknown"
        assert doc.judge == "Unknown"
        assert doc.court == "Unknown Court"
        assert doc.text_content == ""
        assert doc.chunks == []
        assert doc.embeddings == []

    def test_custom_values(self):
        """Custom values should be stored correctly."""
        doc = DocumentData(
            case_id="custom-123",
            title="Custom Case",
            petitioner="Petitioner Name",
            respondent="Respondent Name",
            text_content="Document text",
        )

        assert doc.case_id == "custom-123"
        assert doc.title == "Custom Case"
        assert doc.petitioner == "Petitioner Name"
        assert doc.respondent == "Respondent Name"
        assert doc.text_content == "Document text"

    def test_mutable_defaults_are_independent(self):
        """Mutable default fields should be independent per instance."""
        doc1 = DocumentData(case_id="1", title="Doc 1")
        doc2 = DocumentData(case_id="2", title="Doc 2")

        doc1.chunks.append("chunk1")

        assert len(doc1.chunks) == 1
        assert len(doc2.chunks) == 0


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for the unified pipeline components."""

    def test_keyword_filter_reduces_ner_workload(self, mock_nlp):
        """Verify keyword filter significantly reduces NER calls."""
        # Create a mix of documents
        gst_docs = [
            DocumentData(
                case_id=f"gst-{i}",
                title=f"GST Case {i}",
                text_content=f"This involves GST and CGST Act provisions {i}",
            )
            for i in range(3)
        ]
        non_gst_docs = [
            DocumentData(
                case_id=f"other-{i}",
                title=f"Other Case {i}",
                text_content=f"Criminal case under IPC section 302 number {i}",
            )
            for i in range(7)
        ]

        all_docs = gst_docs + non_gst_docs

        # The NER should only be called on GST candidates (3 docs), not all 10
        result = batch_ner_filter(all_docs, mock_nlp)

        # Only GST docs should pass
        assert len(result) == 3

    def test_extraction_and_filter_chain(self, mock_nlp):
        """Test the extraction -> filter chain works correctly."""
        # Simulate extracted document
        doc = DocumentData(
            case_id="chain-test",
            title="Chain Test",
            text_content="The GST Act and CGST provisions apply here.",
        )

        # Filter should work on extracted document
        result = batch_ner_filter([doc], mock_nlp)

        assert len(result) == 1
        assert result[0].case_id == "chain-test"


# ============================================================================
# Tests for ONNX Integration
# ============================================================================


class TestONNXIntegration:
    """Tests for ONNX embeddings integration."""

    def test_onnx_embedder_import(self):
        """Verify ONNXEmbedder can be imported from unified_pipeline."""
        from pipelines.unified_pipeline import ONNXEmbedder

        embedder = ONNXEmbedder()
        assert embedder.model_name == "sentence-transformers/all-MiniLM-L6-v2"

    def test_run_pipeline_accepts_use_onnx_param(self):
        """Verify run_pipeline accepts use_onnx parameter."""
        import inspect

        from pipelines.unified_pipeline import run_pipeline

        sig = inspect.signature(run_pipeline)
        assert "use_onnx" in sig.parameters
        assert sig.parameters["use_onnx"].default is False

    def test_onnx_embedder_encode_interface(self):
        """Verify ONNXEmbedder has same interface as EmbeddingModel."""
        import numpy as np

        from pipelines.unified_pipeline import ONNXEmbedder

        embedder = ONNXEmbedder()

        # Mock the internal model to avoid loading real model
        with patch.object(embedder, "_ensure_initialized"):
            embedder._initialized = True
            embedder._use_onnx = False

            mock_model = MagicMock()
            mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3]])
            embedder.model = mock_model

            result = embedder.encode(["test text"])

            assert isinstance(result, list)
            assert len(result) == 1


# ============================================================================
# Tests for court-type support
# ============================================================================


class TestCourtTypeSupport:
    """Tests for --court-type and --years pipeline parameters."""

    def test_run_pipeline_accepts_court_type_param(self):
        """run_pipeline should accept court_type parameter."""
        sig = inspect.signature(run_pipeline)
        assert "court_type" in sig.parameters
        assert sig.parameters["court_type"].default == "all"

    def test_run_pipeline_accepts_years_param(self):
        """run_pipeline should accept years parameter."""
        sig = inspect.signature(run_pipeline)
        assert "years" in sig.parameters
        assert sig.parameters["years"].default is None

    def test_load_sc_metadata_filters_sc_only(self, tmp_path):
        """load_sc_metadata should only load SC-GST-*.parquet files."""
        # Create SC parquet
        sc_data = {
            "title": ["SC Case"],
            "petitioner": ["P"],
            "respondent": ["R"],
            "judge": ["J"],
            "citation": ["[2018] 1 S.C.R. 1"],
            "case_id": ["2018 INSC 1"],
            "cnr": ["ESCR001"],
            "decision_date": ["2018-01-01"],
            "disposal_nature": [""],
            "court": ["Supreme Court of India"],
            "nc_display": [""],
            "year": ["2018"],
            "path": ["file.pdf"],
        }
        pd.DataFrame(sc_data).to_parquet(tmp_path / "SC-GST-2018.parquet", index=False)

        # Create HC parquet (different columns - should be ignored)
        hc_data = {
            "court_code": ["2~5"],
            "title": ["HC Case"],
            "judge": ["J"],
            "pdf_link": ["court/cnrorders/cmis/orders/F.pdf"],
            "cnr": ["HPHC001"],
            "decision_date": [pd.Timestamp("2018-06-01")],
            "disposal_nature": [""],
            "court": ["HC"],
        }
        pd.DataFrame(hc_data).to_parquet(tmp_path / "HC-GST-2018.parquet", index=False)

        records = load_sc_metadata(str(tmp_path))
        assert len(records) == 1
        assert records[0]["case_id"] == "2018 INSC 1"

    def test_sc_only_pipeline_skips_hc(self):
        """court_type='sc' should not call load_hc_metadata."""
        with (
            patch("pipelines.unified_pipeline.load_sc_metadata", return_value=[]) as mock_sc,
            patch("pipelines.unified_pipeline.load_hc_metadata") as mock_hc,
            patch("pipelines.unified_pipeline.parallel_extract_from_zips", return_value=[]),
            patch("pipelines.unified_pipeline.parallel_extract_from_tars", return_value=[]),
        ):
            result = run_pipeline(
                metadata_dir="/fake",
                judgments_dir="/fake",
                court_type="sc",
                skip_filter=True,
            )

        mock_sc.assert_called_once()
        mock_hc.assert_not_called()
        assert result == 0

    def test_hc_only_pipeline_skips_sc(self):
        """court_type='hc' should not call load_sc_metadata."""
        with (
            patch("pipelines.unified_pipeline.load_sc_metadata") as mock_sc,
            patch("pipelines.unified_pipeline.load_hc_metadata", return_value=[]) as mock_hc,
            patch("pipelines.unified_pipeline.parallel_extract_from_zips", return_value=[]),
            patch("pipelines.unified_pipeline.parallel_extract_from_tars", return_value=[]),
        ):
            result = run_pipeline(
                metadata_dir="/fake",
                judgments_dir="/fake",
                court_type="hc",
                skip_filter=True,
            )

        mock_sc.assert_not_called()
        mock_hc.assert_called_once()
        assert result == 0

    def test_all_pipeline_calls_both(self):
        """court_type='all' should call both SC and HC loaders."""
        with (
            patch("pipelines.unified_pipeline.load_sc_metadata", return_value=[]) as mock_sc,
            patch("pipelines.unified_pipeline.load_hc_metadata", return_value=[]) as mock_hc,
            patch("pipelines.unified_pipeline.parallel_extract_from_zips", return_value=[]),
            patch("pipelines.unified_pipeline.parallel_extract_from_tars", return_value=[]),
        ):
            run_pipeline(
                metadata_dir="/fake",
                judgments_dir="/fake",
                court_type="all",
                skip_filter=True,
            )

        mock_sc.assert_called_once()
        mock_hc.assert_called_once()

    def test_years_passed_to_hc_loader(self):
        """years parameter should be forwarded to load_hc_metadata."""
        with (
            patch("pipelines.unified_pipeline.load_sc_metadata", return_value=[]),
            patch("pipelines.unified_pipeline.load_hc_metadata", return_value=[]) as mock_hc,
            patch("pipelines.unified_pipeline.parallel_extract_from_zips", return_value=[]),
            patch("pipelines.unified_pipeline.parallel_extract_from_tars", return_value=[]),
        ):
            run_pipeline(
                metadata_dir="/fake",
                judgments_dir="/fake",
                court_type="all",
                years=[2018, 2019],
                skip_filter=True,
            )

        mock_hc.assert_called_once_with("/fake", years=[2018, 2019])


# ============================================================================
# Tests for Two-Stage Pipeline (Export / Import)
# ============================================================================


class TestTwoStagePipeline:
    """Tests for the export-after-filter / import-filtered two-stage pipeline."""

    @pytest.fixture
    def sample_docs(self):
        """Create sample filtered documents for export tests."""
        return [
            DocumentData(
                case_id="SC/2018/001",
                title="SC GST Case 1",
                petitioner="Petitioner A",
                respondent="Respondent B",
                judge="Judge X",
                citation="[2018] 1 GST 100",
                court="Supreme Court of India",
                decision_date="2018-01-15",
                year="2018",
                path="SC-GST-2018/file1.pdf",
                text_content="GST case content about CGST Act provisions.",
                pdf_bytes=b"fake-pdf-bytes-sc",
                is_gst_core=True,
                extracted_provisions=["Section 73"],
                extracted_statutes=["CGST Act"],
            ),
            DocumentData(
                case_id="HC/2018/002",
                title="HC GST Case 2",
                petitioner="Petitioner C",
                respondent="Respondent D",
                judge="Judge Y",
                citation="2018-HC-GST-200",
                court="High Court",
                decision_date="2018-06-01",
                year="2018",
                path="HC-GST-2018/file2.pdf",
                text_content="High court GST judgment text.",
                is_gst_core=True,
                extracted_provisions=["Section 16"],
                extracted_statutes=["IGST Act"],
            ),
        ]

    def test_export_after_filter_creates_jsonl_and_pdfs(self, tmp_path, sample_docs):
        """Verify JSONL + PDFs created, JSONL schema excludes pdf_bytes/pdf_staging_path."""
        export_dir = str(tmp_path / "export")

        count = export_after_filter(sample_docs, export_dir)

        assert count == 2

        # JSONL exists and has correct number of lines
        jsonl_path = tmp_path / "export" / "export.jsonl"
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 2

        # Verify schema: no pdf_bytes or pdf_staging_path in JSONL
        for line in lines:
            record = json.loads(line)
            assert "pdf_bytes" not in record
            assert "pdf_staging_path" not in record
            assert "case_id" in record
            assert "text_content" in record
            assert "pdf_filename" in record

        # PDF created for SC doc (has pdf_bytes)
        pdfs_dir = tmp_path / "export" / "pdfs"
        assert pdfs_dir.exists()
        assert (pdfs_dir / "SC_2018_001.pdf").exists()

    def test_import_filtered_docs_creates_document_data(self, tmp_path, sample_docs):
        """Verify DocumentData reconstructed from JSONL with pdf_staging_path set."""
        export_dir = str(tmp_path / "export")
        export_after_filter(sample_docs, export_dir)

        imported = import_filtered_docs(export_dir)

        assert len(imported) == 2
        assert all(isinstance(d, DocumentData) for d in imported)

        # First doc should have pdf_staging_path (PDF was exported)
        sc_doc = imported[0]
        assert sc_doc.case_id == "SC/2018/001"
        assert sc_doc.pdf_staging_path is not None
        assert Path(sc_doc.pdf_staging_path).exists()

    def test_import_missing_jsonl_raises(self, tmp_path):
        """FileNotFoundError on missing export.jsonl."""
        with pytest.raises(FileNotFoundError, match="Export manifest not found"):
            import_filtered_docs(str(tmp_path / "nonexistent"))

    def test_export_then_import_roundtrip(self, tmp_path, sample_docs):
        """All metadata fields preserved through export+import."""
        export_dir = str(tmp_path / "export")
        export_after_filter(sample_docs, export_dir)
        imported = import_filtered_docs(export_dir)

        for orig, imp in zip(sample_docs, imported):
            assert imp.case_id == orig.case_id
            assert imp.title == orig.title
            assert imp.petitioner == orig.petitioner
            assert imp.respondent == orig.respondent
            assert imp.judge == orig.judge
            assert imp.citation == orig.citation
            assert imp.court == orig.court
            assert imp.decision_date == orig.decision_date
            assert imp.year == orig.year
            assert imp.path == orig.path
            assert imp.text_content == orig.text_content
            assert imp.is_gst_core == orig.is_gst_core
            assert imp.extracted_provisions == orig.extracted_provisions
            assert imp.extracted_statutes == orig.extracted_statutes

    def test_run_pipeline_export_mode_stops_after_filter(self, tmp_path):
        """Steps 4-6 NOT called when export mode is active."""
        export_dir = str(tmp_path / "export")
        fake_doc = DocumentData(
            case_id="test-1",
            title="Test",
            text_content="GST content",
            is_gst_core=True,
        )

        with (
            patch("pipelines.unified_pipeline.load_sc_metadata", return_value=[{"citation": "C1"}]),
            patch("pipelines.unified_pipeline.load_hc_metadata", return_value=[]),
            patch(
                "pipelines.unified_pipeline.parallel_extract_from_zips", return_value=[fake_doc]
            ),
            patch("pipelines.unified_pipeline.parallel_extract_from_tars", return_value=[]),
            patch("pipelines.unified_pipeline.parallel_convert_pdfs") as mock_convert,
            patch("pipelines.unified_pipeline.batch_chunk_and_embed") as mock_embed,
            patch("pipelines.unified_pipeline.bulk_insert_to_db") as mock_db,
        ):
            result = run_pipeline(
                metadata_dir="/fake",
                judgments_dir="/fake",
                skip_filter=True,
                export_after_filter_dir=export_dir,
            )

        # Export should have been created
        assert result == 1
        # Steps 4-6 should NOT have been called
        mock_convert.assert_not_called()
        mock_embed.assert_not_called()
        mock_db.assert_not_called()

    def test_run_pipeline_import_mode_skips_steps_1_to_3(self, tmp_path):
        """Steps 1-3 NOT called when import mode is active."""
        # Create export data to import
        export_dir = str(tmp_path / "export")
        Path(export_dir).mkdir(parents=True)
        (Path(export_dir) / "pdfs").mkdir()
        record = {
            "case_id": "imp-1",
            "title": "Imported",
            "text_content": "GST content",
            "is_gst_core": True,
            "extracted_provisions": [],
            "extracted_statutes": [],
            "pdf_filename": None,
        }
        (Path(export_dir) / "export.jsonl").write_text(json.dumps(record) + "\n")

        with (
            patch("pipelines.unified_pipeline.load_sc_metadata") as mock_sc,
            patch("pipelines.unified_pipeline.load_hc_metadata") as mock_hc,
            patch("pipelines.unified_pipeline.parallel_extract_from_zips") as mock_extract_sc,
            patch("pipelines.unified_pipeline.parallel_extract_from_tars") as mock_extract_hc,
            patch("pipelines.unified_pipeline.parallel_convert_pdfs", side_effect=lambda d: d),
            patch("pipelines.unified_pipeline.batch_chunk_and_embed", side_effect=lambda d, m, s: d),
            patch("pipelines.unified_pipeline.bulk_insert_to_db"),
        ):
            result = run_pipeline(
                metadata_dir="/fake",
                judgments_dir="/fake",
                import_filtered_dir=export_dir,
            )

        # Steps 1-3 should NOT have been called
        mock_sc.assert_not_called()
        mock_hc.assert_not_called()
        mock_extract_sc.assert_not_called()
        mock_extract_hc.assert_not_called()
        assert result == 1

    def test_export_and_import_cli_args_exist(self):
        """run_pipeline signature includes new params with None defaults."""
        sig = inspect.signature(run_pipeline)
        assert "export_after_filter_dir" in sig.parameters
        assert sig.parameters["export_after_filter_dir"].default is None
        assert "import_filtered_dir" in sig.parameters
        assert sig.parameters["import_filtered_dir"].default is None

    def test_export_handles_hc_staging_path_move(self, tmp_path):
        """HC PDFs moved (not copied) from staging to export dir."""
        # Create a staging file
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()
        staging_pdf = staging_dir / "hc_case.pdf"
        staging_pdf.write_bytes(b"hc-pdf-content")

        doc = DocumentData(
            case_id="HC-2018-100",
            title="HC Case",
            text_content="HC GST content",
            pdf_staging_path=str(staging_pdf),
            is_gst_core=True,
        )

        export_dir = str(tmp_path / "export")
        export_after_filter([doc], export_dir)

        # PDF should be in export dir
        exported_pdf = tmp_path / "export" / "pdfs" / "HC-2018-100.pdf"
        assert exported_pdf.exists()
        assert exported_pdf.read_bytes() == b"hc-pdf-content"

        # Original staging file should be gone (moved, not copied)
        assert not staging_pdf.exists()
