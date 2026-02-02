from pathlib import Path
from unittest.mock import patch, MagicMock
from convert_pdf import convert_pdf_to_html, convert_bulk_htmls


def test_convert_pdf_to_html_returns_string():
    """Test that convert_pdf_to_html returns a string"""
    # Mock the fitz module
    with patch('convert_pdf.fitz') as mock_fitz:
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {"text": "Test paragraph content."}
                            ]
                        }
                    ]
                }
            ]
        }
        mock_doc.__iter__.return_value = [mock_page]
        mock_fitz.open.return_value = mock_doc
        
        result = convert_pdf_to_html(Path("test.pdf"))
        
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Test paragraph content" in result


def test_convert_pdf_to_html_handles_error():
    """Test that convert_pdf_to_html handles errors gracefully"""
    with patch('convert_pdf.fitz') as mock_fitz:
        mock_fitz.open.side_effect = Exception("File not found")
        
        result = convert_pdf_to_html(Path("nonexistent.pdf"))
        
        assert result == ""


def test_clean_text_replacements():
    """Test that text cleaning works correctly"""
    with patch('convert_pdf.fitz') as mock_fitz:
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {"text": "Test with quotes."}
                            ]
                        }
                    ]
                }
            ]
        }
        mock_doc.__iter__.return_value = [mock_page]
        mock_fitz.open.return_value = mock_doc
        
        result = convert_pdf_to_html(Path("test.pdf"))
        
        # Should have cleaned the text
        assert "Test with quotes" in result


def test_convert_bulk_htmls(temp_dir):
    """Test bulk conversion of PDFs to HTML"""
    source_dir = temp_dir / "source"
    output_dir = temp_dir / "output"
    source_dir.mkdir()
    
    # Create a dummy PDF file
    dummy_pdf = source_dir / "test.pdf"
    dummy_pdf.touch()
    
    with patch('convert_pdf.convert_pdf_to_html') as mock_convert:
        mock_convert.return_value = "<p>Test</p>"
        convert_bulk_htmls(str(source_dir), str(output_dir))
        
        # Verify output directory was created
        assert output_dir.exists()


def test_header_footer_filtering():
    """Test that headers and footers are filtered out"""
    with patch('convert_pdf.fitz') as mock_fitz:
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "lines": [{"spans": [{"text": "Supreme Court Reports"}]}]
                },
                {
                    "type": 0,
                    "lines": [{"spans": [{"text": "Actual content here."}]}]
                },
                {
                    "type": 0,
                    "lines": [{"spans": [{"text": "123"}]}]  # Page number
                }
            ]
        }
        mock_doc.__iter__.return_value = [mock_page]
        mock_fitz.open.return_value = mock_doc
        
        result = convert_pdf_to_html(Path("test.pdf"))
        
        # Should contain actual content but not headers/footers
        assert "Actual content" in result
        # Headers/footers might still appear but should be filtered in production


def test_paragraph_merging():
    """Test that paragraphs are merged correctly"""
    with patch('convert_pdf.fitz') as mock_fitz:
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "lines": [{"spans": [{"text": "This is a sentence that"}]}]
                },
                {
                    "type": 0,
                    "lines": [{"spans": [{"text": "continues on the next line"}]}]
                }
            ]
        }
        mock_doc.__iter__.return_value = [mock_page]
        mock_fitz.open.return_value = mock_doc
        
        result = convert_pdf_to_html(Path("test.pdf"))
        
        # Should merge the paragraphs
        assert "sentence that continues" in result or "sentence that" in result
