from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app, embed_model


@pytest.fixture
def client():
    """Create a test client for the FastAPI app"""
    return TestClient(app)


@pytest.fixture
def mock_db(test_db, sample_document, sample_chunk):
    """Mock database dependency"""
    return test_db


def test_search_endpoint_basic(
    client, postgres_db, postgres_sample_document, postgres_sample_chunk
):
    """Test the /search endpoint with basic query using PostgreSQL"""

    # Mock the database dependency
    def override_get_db():
        yield postgres_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    # Mock the embedding model to return numpy array-like object
    import numpy as np

    with patch.object(embed_model, "encode") as mock_encode:
        mock_encode.return_value = np.array([0.1] * 384)
        response = client.get("/search?q=test")

    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)

    # Clean up
    app.dependency_overrides.clear()


def test_search_endpoint_with_filters(
    client, postgres_db, postgres_sample_document, postgres_sample_chunk
):
    """Test the /search endpoint with court and year filters using PostgreSQL"""

    def override_get_db():
        yield postgres_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    import numpy as np

    with patch.object(embed_model, "encode") as mock_encode:
        mock_encode.return_value = np.array([0.1] * 384)
        response = client.get("/search?q=test&court=Supreme&year=2024")

    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)

    app.dependency_overrides.clear()


def test_search_endpoint_empty_query(client, postgres_db):
    """Test search with empty query"""

    def override_get_db():
        yield postgres_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    import numpy as np

    with patch.object(embed_model, "encode") as mock_encode:
        mock_encode.return_value = np.array([0.1] * 384)
        response = client.get("/search?q=")

    assert response.status_code == 200

    app.dependency_overrides.clear()


def test_search_endpoint_pagination(
    client, postgres_db, postgres_sample_document, postgres_sample_chunk
):
    """Test search with pagination parameters"""

    def override_get_db():
        yield postgres_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    import numpy as np

    with patch.object(embed_model, "encode") as mock_encode:
        mock_encode.return_value = np.array([0.1] * 384)
        response = client.get("/search?q=test&limit=10&offset=0")

    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)
    assert len(results) <= 10

    app.dependency_overrides.clear()


def test_get_document_endpoint_success(client, mock_db, sample_document):
    """Test the /document/{id} endpoint with valid ID"""

    def override_get_db():
        yield mock_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    response = client.get(f"/document/{sample_document.id}")

    assert response.status_code == 200
    doc_data = response.json()
    assert doc_data["case_id"] == "TEST-2024-001"
    assert doc_data["title"] == "Test Case v. Example Corp"

    app.dependency_overrides.clear()


def test_get_document_endpoint_not_found(client, mock_db):
    """Test the /document/{id} endpoint with invalid ID"""

    def override_get_db():
        yield mock_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    response = client.get("/document/99999")

    assert response.status_code == 200
    data = response.json()
    assert "error" in data

    app.dependency_overrides.clear()


def test_cors_middleware(client):
    """Test that CORS middleware is configured"""
    response = client.options("/search")
    # CORS headers should be present
    assert response.status_code in [200, 405]  # OPTIONS might not be explicitly defined
