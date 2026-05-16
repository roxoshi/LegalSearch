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
def sample_analysis_data():
    """Valid AnalysisJSON field data."""
    return {
        "summary": "This is a GST case about cancellation of registration.",
        "facts": "The petitioner filed for GST registration and it was later cancelled.",
        "issues": "Whether the cancellation of GST registration was valid.",
        "petitioner_arguments": "The petitioner argued that no notice was given before cancellation.",
        "respondent_arguments": "The respondent argued that the cancellation was per procedure.",
        "analysis_of_law": "The court analysed Section 29 of the CGST Act, 2017.",
        "precedent_analysis": "The court cited Union of India v. ABC Ltd. (2020).",
        "courts_reasoning": "The court held that cancellation without notice violates natural justice.",
        "conclusion": "The writ petition is allowed. Cancellation order set aside.",
        "ratio_decidendi": "GST registration cannot be cancelled without prior notice to the taxpayer.",
    }


@pytest.fixture
def sample_metadata_data():
    """Valid MetadataJSON field data."""
    return {
        "case_id": "TEST-2024-001",
        "title": "Test Petitioner v. Test Respondent",
        "petitioner": "Test Petitioner",
        "respondent": "Test Respondent",
        "judge": "Justice Test",
        "citation": "2024 TEST 123",
        "court": "Supreme Court of India",
        "decision_date": "2024-01-15",
    }


# Keep old fixture name for any tests that still reference it
@pytest.fixture
def sample_json_data(sample_analysis_data):
    """Alias for backward compatibility."""
    return sample_analysis_data
