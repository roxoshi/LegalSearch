import pytest

from etl.schemas import DocumentJSON


def test_document_json_validation_success(sample_json_data):
    """Test that DocumentJSON validates correct data"""
    doc = DocumentJSON(**sample_json_data)

    assert doc.title == "Test Case v. Example"
    assert doc.case_id == "TEST-001"
    assert doc.text_content == "This is test legal content for the case."


def test_document_json_validation_missing_field():
    """Test that DocumentJSON fails with missing required fields"""
    incomplete_data = {
        "title": "Test Case",
        # Missing other required fields
    }

    with pytest.raises(Exception):  # Pydantic ValidationError
        DocumentJSON(**incomplete_data)  # type: ignore[arg-type]


def test_document_json_all_fields(sample_json_data):
    """Test that all fields are properly set"""
    doc = DocumentJSON(**sample_json_data)

    assert doc.title is not None
    assert doc.petitioner is not None
    assert doc.respondent is not None
    assert doc.judge is not None
    assert doc.citation is not None
    assert doc.decision_date is not None
    assert doc.court is not None
    assert doc.case_id is not None
    assert doc.text_content is not None
