from unittest.mock import Mock, patch

import pytest

from etl.schemas import DocumentJSON
from etl.transform import Transformer


@pytest.fixture
def transformer():
    """Create a Transformer instance with mocked model"""
    with patch("etl.transform.SentenceTransformer") as mock_st:
        mock_model = Mock()
        mock_model.encode.return_value = [[0.1] * 384]  # Mock embedding
        mock_st.return_value = mock_model

        transformer = Transformer(model_name="test-model")
        return transformer


def test_transformer_initialization():
    """Test that Transformer initializes correctly"""
    with patch("etl.transform.SentenceTransformer") as mock_st:
        transformer = Transformer(model_name="test-model")

        assert transformer is not None
        mock_st.assert_called_once_with("test-model")


def test_process_document_basic(transformer, sample_json_data, temp_dir):
    """Test basic document processing"""
    doc_json = DocumentJSON(**sample_json_data)

    # Create a dummy PDF
    pdf_path = temp_dir / "test.pdf"
    pdf_path.touch()

    with patch("etl.transform.convert_pdf_to_html") as mock_convert:
        mock_convert.return_value = "<p>Test HTML content</p>"

        # Mock encode to return numpy arrays
        import numpy as np

        transformer.model.encode = Mock(return_value=[np.array([0.1] * 384)])

        db_doc, db_chunks = transformer.process_document(doc_json, pdf_path)

        assert db_doc is not None
        assert db_doc.title == "Test Case v. Example"
        assert db_doc.case_id == "TEST-001"
        assert db_doc.display_content == "<p>Test HTML content</p>"

        assert isinstance(db_chunks, list)
        assert len(db_chunks) > 0


def test_process_document_no_pdf(transformer, sample_json_data, temp_dir):
    """Test document processing when PDF doesn't exist"""
    doc_json = DocumentJSON(**sample_json_data)

    # Non-existent PDF path
    pdf_path = temp_dir / "nonexistent.pdf"

    # Mock encode to return numpy arrays
    import numpy as np

    transformer.model.encode = Mock(return_value=[np.array([0.1] * 384)])

    db_doc, db_chunks = transformer.process_document(doc_json, pdf_path)

    assert db_doc is not None
    assert db_doc.title == "Test Case v. Example"
    # Should have fallback display content
    assert db_doc.display_content is not None
    assert "This is test legal content" in db_doc.display_content


def test_process_document_empty_content(transformer, sample_json_data, temp_dir):
    """Test document processing with empty text content"""
    sample_json_data["text_content"] = ""
    doc_json = DocumentJSON(**sample_json_data)

    pdf_path = temp_dir / "test.pdf"
    pdf_path.touch()

    with patch("etl.transform.convert_pdf_to_html") as mock_convert:
        mock_convert.return_value = "<p>HTML content</p>"

        db_doc, db_chunks = transformer.process_document(doc_json, pdf_path)

        assert db_doc is not None
        # Should return empty chunks list
        assert len(db_chunks) == 0


def test_chunking_creates_embeddings(transformer, sample_json_data, temp_dir):
    """Test that chunking creates proper embeddings"""
    # Add more content to ensure chunking
    sample_json_data["text_content"] = "This is a test. " * 100
    doc_json = DocumentJSON(**sample_json_data)

    pdf_path = temp_dir / "test.pdf"
    pdf_path.touch()

    with patch("etl.transform.convert_pdf_to_html") as mock_convert:
        mock_convert.return_value = "<p>HTML</p>"

        # Mock the encode method to return proper embeddings (list of lists)
        import numpy as np

        mock_embeddings = [np.array([0.1] * 384) for _ in range(10)]
        transformer.model.encode = Mock(return_value=mock_embeddings)

        db_doc, db_chunks = transformer.process_document(doc_json, pdf_path)

        assert len(db_chunks) > 0
        for chunk in db_chunks:
            assert chunk.chunk_content is not None
            assert chunk.embedding is not None
            assert len(chunk.embedding) == 384


def test_pdf_conversion_error_handling(transformer, sample_json_data, temp_dir):
    """Test that PDF conversion errors are handled gracefully"""
    doc_json = DocumentJSON(**sample_json_data)
    pdf_path = temp_dir / "test.pdf"
    pdf_path.touch()

    with patch("etl.transform.convert_pdf_to_html") as mock_convert:
        mock_convert.side_effect = Exception("PDF conversion failed")

        # Mock encode to return numpy arrays
        import numpy as np

        transformer.model.encode = Mock(return_value=[np.array([0.1] * 384)])

        db_doc, db_chunks = transformer.process_document(doc_json, pdf_path)

        # Should still create document with fallback content
        assert db_doc is not None
        assert db_doc.display_content is not None
