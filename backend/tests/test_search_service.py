from unittest.mock import Mock
from app.search_service import search_legal_cases


def test_search_legal_cases_basic(postgres_db, postgres_sample_document, postgres_sample_chunk):
    """Test basic search functionality with PostgreSQL"""
    # Mock the model
    mock_model = Mock()
    mock_model.encode.return_value.tolist.return_value = [0.1] * 384
    
    results = search_legal_cases(
        db=postgres_db,
        model=mock_model,
        query_text="test legal case",
        limit=50
    )
    
    assert isinstance(results, list)


def test_search_legal_cases_with_limit(postgres_db, postgres_sample_document, postgres_sample_chunk):
    """Test search with limit parameter"""
    mock_model = Mock()
    mock_model.encode.return_value.tolist.return_value = [0.1] * 384
    
    results = search_legal_cases(
        db=postgres_db,
        model=mock_model,
        query_text="test",
        limit=5
    )
    
    assert isinstance(results, list)
    assert len(results) <= 5


def test_search_legal_cases_empty_db(postgres_db):
    """Test search on empty database"""
    mock_model = Mock()
    mock_model.encode.return_value.tolist.return_value = [0.1] * 384
    
    results = search_legal_cases(
        db=postgres_db,
        model=mock_model,
        query_text="test",
        limit=50
    )
    
    assert isinstance(results, list)
    assert len(results) == 0


def test_search_legal_cases_result_format(postgres_db, postgres_sample_document, postgres_sample_chunk):
    """Test that results have the correct format"""
    mock_model = Mock()
    mock_model.encode.return_value.tolist.return_value = [0.1] * 384
    
    results = search_legal_cases(
        db=postgres_db,
        model=mock_model,
        query_text="test",
        limit=10
    )
    
    if results:
        result = results[0]
        assert "case_title" in result
        assert "citation" in result
        assert "snippet" in result
        assert "case_id" in result
