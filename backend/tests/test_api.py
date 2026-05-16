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


def test_cors_allows_lan_origin(client):
    """LAN IP origins must be allowed so docker-compose/local-network access works."""
    import re
    from app.main import _LAN_ORIGIN_REGEX

    lan_origins = [
        "http://192.168.1.4:3000",
        "http://192.168.0.100:3000",
        "http://10.0.0.5:3000",
        "http://172.16.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    pattern = re.compile(_LAN_ORIGIN_REGEX)
    for origin in lan_origins:
        assert pattern.fullmatch(origin), f"LAN origin not matched by regex: {origin}"


# ==================== PDF Download Endpoint ====================


def test_download_pdf_not_found_document(client, mock_db):
    """GET /document/{id}/pdf returns 404 when document id does not exist"""

    def override_get_db():
        yield mock_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    response = client.get("/document/99999/pdf")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

    app.dependency_overrides.clear()


def test_download_pdf_no_pdf_file(client, mock_db, sample_document):
    """GET /document/{id}/pdf returns 404 when PDF file does not exist on disk"""

    def override_get_db():
        yield mock_db

    from app.database import get_database
    from app.main import PDF_DIR

    app.dependency_overrides[get_database] = override_get_db

    # Ensure the PDF file does NOT exist by pointing PDF_DIR at a temp location
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.main.PDF_DIR", Path(tmpdir)):
            response = client.get(f"/document/{sample_document.id}/pdf")

    assert response.status_code == 404
    assert "not available" in response.json()["detail"].lower()

    app.dependency_overrides.clear()


def test_download_pdf_success(client, mock_db, sample_document):
    """GET /document/{id}/pdf returns 200 with application/pdf when file exists"""

    def override_get_db():
        yield mock_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Create a dummy PDF file matching the document's case_id
        pdf_file = tmp_path / f"{sample_document.case_id}.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake pdf content")

        with patch("app.main.PDF_DIR", tmp_path):
            response = client.get(f"/document/{sample_document.id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    app.dependency_overrides.clear()


def test_search_returns_up_to_ten_results(
    client, postgres_db, postgres_sample_document, postgres_sample_chunk
):
    """Search result limit is 10, not 5."""

    def override_get_db():
        yield postgres_db

    from app.database import get_database

    app.dependency_overrides[get_database] = override_get_db

    import numpy as np

    with patch.object(embed_model, "encode") as mock_encode:
        mock_encode.return_value = np.array([0.1] * 384)
        response = client.get("/search?q=gst")

    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)
    assert len(results) <= 10  # max 10, not 5

    app.dependency_overrides.clear()


# ==================== Library Endpoints ====================


def _override_db(db):
    """Return a FastAPI dependency override that yields db."""
    from app.database import get_database

    def _override():
        yield db

    app.dependency_overrides[get_database] = _override


def _clear():
    app.dependency_overrides.clear()


# ── /library/acts ─────────────────────────────────────────────────────────────


def test_list_acts_empty(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/acts")
    _clear()
    assert response.status_code == 200
    assert response.json() == []


def test_list_acts_returns_inserted(client, postgres_db, library_act):
    _override_db(postgres_db)
    response = client.get("/library/acts")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["primary_id"] == 9001
    assert data[0]["act_name"] == "CGST Act"
    assert data[0]["section_no"] == "Section 1"


def test_list_acts_filter_by_act_name(client, postgres_db, library_act):
    _override_db(postgres_db)
    response = client.get("/library/acts?act_name=CGST")
    _clear()
    assert response.status_code == 200
    assert len(response.json()) == 1

    _override_db(postgres_db)
    response = client.get("/library/acts?act_name=IGST")
    _clear()
    assert response.json() == []


def test_list_acts_filter_by_chapter(client, postgres_db, library_act):
    _override_db(postgres_db)
    response = client.get("/library/acts?chapter_no=Chapter+I")
    _clear()
    assert response.status_code == 200
    assert len(response.json()) == 1


# ── /library/acts/{primary_id} ────────────────────────────────────────────────


def test_get_act_found(client, postgres_db, library_act):
    _override_db(postgres_db)
    response = client.get(f"/library/acts/{library_act.primary_id}")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert data["primary_id"] == 9001
    assert data["act_name"] == "CGST Act"
    assert "html_content" in data
    assert "cross_references" in data
    assert isinstance(data["cross_references"], list)


def test_get_act_not_found(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/acts/99999")
    _clear()
    assert response.status_code == 404


def test_get_act_includes_cross_references(client, postgres_db, library_cross_ref, library_act):
    _override_db(postgres_db)
    response = client.get(f"/library/acts/{library_act.primary_id}")
    _clear()
    assert response.status_code == 200
    xrefs = response.json()["cross_references"]
    assert len(xrefs) == 1
    xref = xrefs[0]
    assert xref["target_type"] == "notification"
    assert xref["target_id"] == 9003
    assert xref["anchor_text"] == "Notification 01/2017"
    # resolved fields
    assert xref["label"] == "01/2017-CT"
    assert xref["title"] == "Notification on commencement"


# ── /library/rules/{primary_id} ───────────────────────────────────────────────


def test_get_rule_found(client, postgres_db, library_rule):
    _override_db(postgres_db)
    response = client.get(f"/library/rules/{library_rule.primary_id}")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert data["primary_id"] == 9002
    assert data["act_name"] == "CGST Rules"
    assert data["section_no"] == "Rule 1"
    assert "cross_references" in data


def test_get_rule_not_found(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/rules/99999")
    _clear()
    assert response.status_code == 404


# ── /library/notifications/{primary_id} ───────────────────────────────────────


def test_get_notification_found(client, postgres_db, library_notification):
    _override_db(postgres_db)
    response = client.get(f"/library/notifications/{library_notification.primary_id}")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert data["primary_id"] == 9003
    assert data["notification_no"] == "01/2017-CT"
    assert data["category"] == "Central Tax"
    assert data["is_active"] is True


def test_get_notification_not_found(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/notifications/99999")
    _clear()
    assert response.status_code == 404


# ── /library/circulars/{primary_id} ───────────────────────────────────────────


def test_get_circular_found(client, postgres_db, library_circular):
    _override_db(postgres_db)
    response = client.get(f"/library/circulars/{library_circular.primary_id}")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert data["primary_id"] == 9004
    assert data["circular_no"] == "1/1/2017"
    assert data["category"] == "CGST"
    assert data["is_amended"] is False


def test_get_circular_not_found(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/circulars/99999")
    _clear()
    assert response.status_code == 404


# ── /library/resolve/{type}/{primary_id} ──────────────────────────────────────


def test_resolve_act(client, postgres_db, library_act):
    _override_db(postgres_db)
    response = client.get(f"/library/resolve/act/{library_act.primary_id}")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "act"
    assert data["document"]["primary_id"] == 9001


def test_resolve_notification(client, postgres_db, library_notification):
    _override_db(postgres_db)
    response = client.get(f"/library/resolve/notification/{library_notification.primary_id}")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "notification"
    assert data["document"]["notification_no"] == "01/2017-CT"


def test_resolve_circular(client, postgres_db, library_circular):
    _override_db(postgres_db)
    response = client.get(f"/library/resolve/circular/{library_circular.primary_id}")
    _clear()
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "circular"
    assert data["document"]["circular_no"] == "1/1/2017"


def test_resolve_invalid_type(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/resolve/judgment/1")
    _clear()
    assert response.status_code == 400


def test_resolve_not_found(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/resolve/act/99999")
    _clear()
    assert response.status_code == 404


# ── /library/search ───────────────────────────────────────────────────────────


def test_library_search_returns_results(client, postgres_db, library_act, library_notification):
    _override_db(postgres_db)
    response = client.get("/library/search?q=CGST")
    _clear()
    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)
    # At least the act should match (contains "CGST")
    types_found = {r["type"] for r in results}
    assert "act" in types_found


def test_library_search_type_filter(client, postgres_db, library_act, library_notification):
    _override_db(postgres_db)
    response = client.get("/library/search?q=CGST&types=notification")
    _clear()
    assert response.status_code == 200
    results = response.json()
    assert all(r["type"] == "notification" for r in results)


def test_library_search_short_query_rejected(client, postgres_db):
    _override_db(postgres_db)
    response = client.get("/library/search?q=a")
    _clear()
    assert response.status_code == 422  # min_length=2 validation


def test_library_search_result_shape(client, postgres_db, library_act):
    _override_db(postgres_db)
    response = client.get("/library/search?q=CGST&types=act")
    _clear()
    assert response.status_code == 200
    results = response.json()
    if results:
        hit = results[0]
        assert "type" in hit
        assert "primary_id" in hit
        assert "label" in hit
