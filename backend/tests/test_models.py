import pytest
from app.models import Document


def test_document_creation(test_db, sample_document):
    """Test that a document can be created and retrieved"""
    assert sample_document.id is not None
    assert sample_document.title == "Test Case v. Example Corp"
    assert sample_document.case_id == "TEST-2024-001"
    
    # Verify it's in the database
    retrieved = test_db.query(Document).filter_by(case_id="TEST-2024-001").first()
    assert retrieved is not None
    assert retrieved.title == sample_document.title


def test_document_chunk_creation(test_db, sample_chunk, sample_document):
    """Test that a document chunk can be created with embeddings"""
    assert sample_chunk.id is not None
    assert sample_chunk.document_id == sample_document.id
    assert len(sample_chunk.embedding) == 384
    
    # Test relationship
    assert sample_chunk.document.title == sample_document.title


def test_document_relationship(test_db, sample_document, sample_chunk):
    """Test the relationship between Document and DocumentChunk"""
    # Refresh to load relationships
    test_db.refresh(sample_document)
    
    assert len(sample_document.chunks) == 1
    assert sample_document.chunks[0].id == sample_chunk.id
    assert sample_document.chunks[0].chunk_content == "This is a test chunk content."


def test_unique_case_id(test_db, sample_document):
    """Test that case_id is unique"""
    duplicate_doc = Document(
        title="Another Case",
        petitioner="Another Petitioner",
        respondent="Another Respondent",
        judge="Another Judge",
        citation="2024 TEST 456",
        decision_date="2024-02-15",
        court="High Court",
        case_id="TEST-2024-001",  # Same case_id
        content="Different content",
        display_content="<p>Different content</p>"
    )
    test_db.add(duplicate_doc)
    
    with pytest.raises(Exception):  # Should raise IntegrityError
        test_db.commit()
    
    test_db.rollback()


def test_document_required_fields(test_db):
    """Test that required fields are enforced"""
    incomplete_doc = Document(
        title="Incomplete Case"
        # Missing required fields
    )
    test_db.add(incomplete_doc)
    
    with pytest.raises(Exception):
        test_db.commit()
    
    test_db.rollback()
