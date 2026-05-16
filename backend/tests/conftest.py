import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import CompileError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Act, Circular, CrossReference, DocType, Document, DocumentChunk, Notification, Rule


@pytest.fixture(scope="function")
def test_db():
    """Create a test database for each test.

    Uses SQLite in-memory. Skips if models use PostgreSQL-specific types
    (ARRAY, pgvector) that SQLite cannot handle.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(bind=engine)
    except CompileError:
        pytest.skip("Models use PostgreSQL-specific types not supported by SQLite")
    TestingSessionLocal = sessionmaker(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sample_document(test_db):
    """Create a sample document for testing"""
    doc = Document(
        title="Test Case v. Example Corp",
        petitioner="Test Case",
        respondent="Example Corp",
        judge="Justice Test",
        citation="2024 TEST 123",
        decision_date="2024-01-15",
        court="Supreme Court",
        case_id="TEST-2024-001",
        content="This is a test legal document content.",
        display_content="<p>This is a test legal document content.</p>",
    )
    test_db.add(doc)
    test_db.commit()
    test_db.refresh(doc)
    return doc


@pytest.fixture
def sample_chunk(test_db, sample_document):
    """Create a sample document chunk for testing"""
    # Create a dummy 384-dimensional embedding
    embedding = [0.1] * 384
    chunk = DocumentChunk(
        document_id=sample_document.id,
        chunk_content="This is a test chunk content.",
        embedding=embedding,
    )
    test_db.add(chunk)
    test_db.commit()
    test_db.refresh(chunk)
    return chunk


# PostgreSQL fixtures for pgvector tests
def is_postgres_available():
    """Check if PostgreSQL test database is available"""
    try:
        test_url = os.getenv(
            "DATABASE_URL", "postgresql://testuser:testpass@localhost:5433/legalsearch_test"
        )
        engine = create_engine(test_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except (OperationalError, Exception):
        return False


@pytest.fixture(scope="function")
def postgres_db():
    """
    PostgreSQL database fixture for tests requiring pgvector.
    Skips if PostgreSQL is not available.
    """
    if not is_postgres_available():
        pytest.skip(
            "PostgreSQL test database not available. Run: docker-compose -f docker-compose.test.yml up -d"
        )

    # Create engine for PostgreSQL
    test_url = os.getenv(
        "DATABASE_URL", "postgresql://testuser:testpass@localhost:5433/legalsearch_test"
    )
    engine = create_engine(test_url)

    # Create tables
    Base.metadata.create_all(engine)

    # Create session
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    yield session

    # Cleanup
    session.close()

    # Drop all tables
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def postgres_sample_document(postgres_db):
    """Create a sample document in PostgreSQL"""
    doc = Document(
        title="Test Case v. Example Corp",
        petitioner="Test Petitioner",
        respondent="Test Respondent",
        judge="Test Judge",
        citation="2024 TEST 001",
        decision_date="2024-01-01",
        court="Supreme Court",
        case_id="TEST-2024-001",
        content="This is test content for the legal case.",
        display_content="<p>This is test content for the legal case.</p>",
    )
    postgres_db.add(doc)
    postgres_db.commit()
    postgres_db.refresh(doc)
    return doc


@pytest.fixture
def postgres_sample_chunk(postgres_db, postgres_sample_document):
    """Create a sample chunk with embedding in PostgreSQL"""
    # Create a simple embedding vector (384 dimensions for all-MiniLM-L6-v2)
    embedding = [0.1] * 384

    chunk = DocumentChunk(
        document_id=postgres_sample_document.id,
        chunk_content="This is test content for the legal case.",
        embedding=embedding,
    )
    postgres_db.add(chunk)
    postgres_db.commit()
    postgres_db.refresh(chunk)
    return chunk


# ─────────────────────────────────────────────────────────────────────────────
# Library fixtures (require PostgreSQL via postgres_db)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def library_act(postgres_db):
    row = Act(
        primary_id=9001,
        content_id=1111900001,
        act_name="CGST Act",
        chapter_no="Chapter I",
        chapter_name="Preliminary",
        section_no="Section 1",
        section_name="Short title",
        content="This Act may be called the CGST Act.",
        html_content="<p>This Act may be called the CGST Act.</p>",
        source_url="https://example.com/cgst/s1",
    )
    postgres_db.add(row)
    postgres_db.commit()
    postgres_db.refresh(row)
    return row


@pytest.fixture
def library_rule(postgres_db):
    row = Rule(
        primary_id=9002,
        content_id=1220900002,
        act_name="CGST Rules",
        chapter_id=1,
        section_no="Rule 1",
        section_name="Short title",
        content="These rules may be called the CGST Rules.",
        html_content="<p>These rules may be called the CGST Rules.</p>",
        source_url="https://example.com/cgst-rules/r1",
    )
    postgres_db.add(row)
    postgres_db.commit()
    postgres_db.refresh(row)
    return row


@pytest.fixture
def library_notification(postgres_db):
    from datetime import datetime, timezone
    row = Notification(
        primary_id=9003,
        content_id=1500900003,
        notification_no="01/2017-CT",
        issued_on=datetime(2017, 6, 19, tzinfo=timezone.utc),
        title="Notification on commencement",
        content="In exercise of powers under CGST Act...",
        category="Central Tax",
        year=2017,
        is_active=True,
        is_amended=False,
    )
    postgres_db.add(row)
    postgres_db.commit()
    postgres_db.refresh(row)
    return row


@pytest.fixture
def library_circular(postgres_db):
    from datetime import datetime, timezone
    row = Circular(
        primary_id=9004,
        content_id=1600900004,
        circular_no="1/1/2017",
        issued_on=datetime(2017, 9, 26, tzinfo=timezone.utc),
        subject="Issues related to furnishing of Bond/LUT",
        content="Various representations have been received...",
        category="CGST",
        year=2017,
        is_active=True,
        is_amended=False,
    )
    postgres_db.add(row)
    postgres_db.commit()
    postgres_db.refresh(row)
    return row


@pytest.fixture
def library_cross_ref(postgres_db, library_act, library_notification):
    """A cross-reference from library_act → library_notification."""
    row = CrossReference(
        source_type=DocType.act,
        source_id=library_act.primary_id,
        target_type=DocType.notification,
        target_id=library_notification.primary_id,
        anchor_text="Notification 01/2017",
    )
    postgres_db.add(row)
    postgres_db.commit()
    postgres_db.refresh(row)
    return row
