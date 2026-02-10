from app.custom_types import SearchResult


def test_search_result_creation():
    """Test SearchResult creation"""
    result = SearchResult(
        id=1,
        chunk_id=10,
        case_id="TEST-001",
        title="Test Case",
        citation="2024 TEST 1",
        content="Test content",
        rrf_score=0.95,
    )

    assert result.id == 1
    assert result.chunk_id == 10
    assert result.case_id == "TEST-001"
    assert result.title == "Test Case"
    assert result.citation == "2024 TEST 1"
    assert result.content == "Test content"
    assert result.rrf_score == 0.95


def test_search_result_fields():
    """Test that SearchResult has all required fields"""
    result = SearchResult(
        id=1,
        chunk_id=2,
        case_id="ID",
        title="Title",
        citation="Citation",
        content="Content",
        rrf_score=0.5,
    )

    assert hasattr(result, "id")
    assert hasattr(result, "chunk_id")
    assert hasattr(result, "case_id")
    assert hasattr(result, "title")
    assert hasattr(result, "citation")
    assert hasattr(result, "content")
    assert hasattr(result, "rrf_score")
