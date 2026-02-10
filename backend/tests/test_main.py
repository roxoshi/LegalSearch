import pytest
from fastapi.testclient import TestClient

from app.main import app, embed_model


@pytest.fixture
def client():
    """Create a test client"""
    return TestClient(app)


def test_app_creation():
    """Test that FastAPI app is created"""
    assert app is not None
    assert app.title == "FastAPI"


def test_embed_model_exists():
    """Test that embedding model is initialized"""
    assert embed_model is not None


def test_cors_middleware_configured():
    """Test that CORS middleware is configured"""
    # Check that middleware is in the app
    assert len(app.user_middleware) > 0


def test_root_endpoint_not_defined(client):
    """Test that root endpoint returns 404"""
    response = client.get("/")
    assert response.status_code == 404


def test_health_check_via_docs(client):
    """Test that API docs are accessible"""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema(client):
    """Test that OpenAPI schema is accessible"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "openapi" in schema
    assert "paths" in schema
