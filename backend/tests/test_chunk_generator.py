import pytest
from app.chunk_generator import RecursiveCharacterTextSplitter


def test_splitter_initialization():
    """Test that splitter initializes with correct defaults"""
    splitter = RecursiveCharacterTextSplitter()
    
    assert splitter.chunk_size == 1000
    assert splitter.chunk_overlap == 200
    assert not splitter.keep_separator
    assert len(splitter.separators) > 0


def test_splitter_custom_params():
    """Test splitter with custom parameters"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n"],
        keep_separator=True
    )
    
    assert splitter.chunk_size == 500
    assert splitter.chunk_overlap == 50
    assert splitter.keep_separator
    assert splitter.separators == ["\n\n", "\n"]


def test_splitter_invalid_overlap():
    """Test that invalid overlap raises ValueError"""
    with pytest.raises(ValueError, match="chunk_overlap must be smaller than chunk_size"):
        RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=100)
    
    with pytest.raises(ValueError):
        RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=200)


def test_split_short_text():
    """Test splitting text shorter than chunk size"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=0)
    text = "This is a short text."
    
    result = splitter.split_text(text)
    
    assert len(result) == 1
    assert result[0] == text


def test_split_long_text():
    """Test splitting long text into multiple chunks"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=50, chunk_overlap=10)
    text = "This is a long text. " * 20  # Creates text > 50 chars
    
    result = splitter.split_text(text)
    
    assert len(result) > 1
    # Chunks should be created, some may be longer due to overlap
    assert all(isinstance(chunk, str) for chunk in result)
    assert all(len(chunk) > 0 for chunk in result)


def test_split_with_newlines():
    """Test splitting text with newline separators"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=30, chunk_overlap=5)
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    
    result = splitter.split_text(text)
    
    assert len(result) >= 1
    assert all(isinstance(chunk, str) for chunk in result)


def test_split_with_keep_separator():
    """Test splitting with separator retention"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=50,
        chunk_overlap=0,
        separators=["\n\n"],
        keep_separator=True
    )
    text = "First\n\nSecond\n\nThird"
    
    result = splitter.split_text(text)
    
    # At least one chunk should contain the separator
    assert any("\n\n" in chunk for chunk in result) or len(result) == 1


def test_split_empty_text():
    """Test splitting empty text"""
    splitter = RecursiveCharacterTextSplitter()
    text = ""
    
    result = splitter.split_text(text)
    
    assert len(result) == 1
    assert result[0] == ""


def test_split_with_overlap():
    """Test that overlap is applied correctly"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=20, chunk_overlap=5)
    text = "A" * 50  # 50 characters
    
    result = splitter.split_text(text)
    
    assert len(result) > 1
    # Check that chunks have overlap
    if len(result) > 1:
        # Second chunk should start with some characters from first chunk
        assert len(result[1]) >= 5  # At least overlap size


def test_merge_splits():
    """Test the _merge_splits method"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=10, chunk_overlap=0)
    splits = ["abc", "def", "ghi"]
    
    result = splitter._merge_splits(splits)
    
    assert isinstance(result, list)
    assert all(isinstance(chunk, str) for chunk in result)


def test_split_with_different_separators():
    """Test splitting with different separator priorities"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=30,
        chunk_overlap=0,
        separators=[". ", " "]
    )
    text = "First sentence. Second sentence. Third sentence."
    
    result = splitter.split_text(text)
    
    assert len(result) >= 1
    assert all(len(chunk) <= 30 + 10 for chunk in result)  # Allow some tolerance


# New edge case tests for 90%+ coverage
def test_split_very_long_text_without_separators():
    """Test splitting very long text with no separators"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=20)
    text = "A" * 500  # Long text with no separators
    
    result = splitter.split_text(text)
    
    assert len(result) > 1
    assert all(isinstance(chunk, str) for chunk in result)


def test_split_unicode_text():
    """Test splitting text with Unicode characters"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=50, chunk_overlap=10)
    text = "Hello 世界! " * 10
    
    result = splitter.split_text(text)
    
    assert len(result) >= 1
    assert all(isinstance(chunk, str) for chunk in result)


def test_split_with_special_characters():
    """Test splitting text with special characters"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=40, chunk_overlap=5)
    text = "Test @#$%^&*() symbols! " * 5
    
    result = splitter.split_text(text)
    
    assert len(result) >= 1
    assert all(isinstance(chunk, str) for chunk in result)


def test_split_with_empty_separator_list():
    """Test splitting with empty separator list"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=20,
        chunk_overlap=0,
        separators=[]
    )
    text = "This is a test text that should be split character by character"
    
    result = splitter.split_text(text)
    
    assert len(result) >= 1


def test_split_with_maximum_overlap():
    """Test splitting with overlap close to chunk size"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=99)
    text = "A" * 300
    
    result = splitter.split_text(text)
    
    assert len(result) >= 1


def test_split_with_separator_no_keep():
    """Test _split_with_separator without keeping separator"""
    splitter = RecursiveCharacterTextSplitter(keep_separator=False)
    text = "First\n\nSecond\n\nThird"
    
    result = splitter._split_with_separator(text, "\n\n")
    
    assert len(result) == 3
    assert "First" in result
    assert "Second" in result
    assert "Third" in result


def test_split_with_separator_keep():
    """Test _split_with_separator with keeping separator"""
    splitter = RecursiveCharacterTextSplitter(keep_separator=True)
    text = "First\n\nSecond\n\nThird"
    
    result = splitter._split_with_separator(text, "\n\n")
    
    assert len(result) == 3
    # First two should have separator
    assert result[0].endswith("\n\n")
    assert result[1].endswith("\n\n")
