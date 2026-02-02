import pytest
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from app.models import Base, Document, DocumentChunk


def is_postgres_available():
    """Check if PostgreSQL test database is available"""
    try:
        test_url = os.getenv("DATABASE_URL", "postgresql://testuser:testpass@localhost:5433/legalsearch_test")
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
        pytest.skip("PostgreSQL test database not available. Run: docker-compose -f docker-compose.test.yml up -d")
    
    # Create engine for PostgreSQL
    test_url = os.getenv("DATABASE_URL", "postgresql://testuser:testpass@localhost:5433/legalsearch_test")
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
        display_content="<p>This is test content for the legal case.</p>"
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
        embedding=embedding
    )
    postgres_db.add(chunk)
    postgres_db.commit()
    postgres_db.refresh(chunk)
    return chunk
