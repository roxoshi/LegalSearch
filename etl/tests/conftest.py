import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files"""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_json_data():
    """Sample JSON data for testing"""
    return {
        "title": "Test Case v. Example",
        "petitioner": "Test Petitioner",
        "respondent": "Test Respondent",
        "judge": "Test Judge",
        "citation": "2024 TEST 001",
        "decision_date": "2024-01-01",
        "court": "Test Court",
        "case_id": "TEST-001",
        "text_content": "This is test legal content for the case.",
    }
