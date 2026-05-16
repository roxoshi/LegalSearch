import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _get_db_url() -> str:
    """Build the database URL.

    Priority:
    1. DATABASE_URL env var (used as-is)
    2. POSTGRES_USER + POSTGRES_PASSWORD + POSTGRES_HOST + POSTGRES_DB (URL-encoded)
       This is the preferred path in Docker so special characters in passwords
       (e.g. '+') are percent-encoded and not misinterpreted by the URL parser.
    3. Hard-coded local dev default
    """
    if url := os.getenv("DATABASE_URL"):
        return url
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if user and password:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "search_db")
        return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}"
    return "postgresql://user:pass@localhost:5432/search_db"


SQLALCHEMY_DATABASE_URL = _get_db_url()

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_database():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
