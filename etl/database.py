import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import models from backend if in pythonpath, or redefine base
# To ensure standalone capability without brittle relative imports if backend moves,
# we might want to ensure backend is accessible or copy the model definition.
# For now, assuming PYTHONPATH will include project root.

try:
    from backend.app import models as _models  # noqa: F401 — registers all models with Base
    from backend.app.models import Base, Document
except ImportError:
    from app import models as _models  # type: ignore[no-redef]  # noqa: F401
    from app.models import Base, Document  # type: ignore[no-redef]  # noqa: F401

logger = logging.getLogger(__name__)


def get_db_url() -> str:
    """Return the database URL.

    Priority:
    1. DATABASE_URL env var (used as-is)
    2. POSTGRES_USER + POSTGRES_PASSWORD + POSTGRES_DB env vars (URL-encoded)
    3. Hard-coded local dev default

    Using individual POSTGRES_* vars is recommended for passwords that contain
    special characters (e.g. '+'), since those must be percent-encoded in a URL.
    """
    if url := os.getenv("DATABASE_URL"):
        return url

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if user and password:
        from urllib.parse import quote_plus

        db = os.getenv("POSTGRES_DB", "search_db")
        port = os.getenv("DB_PORT", "5432")
        return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@localhost:{port}/{db}"

    return "postgresql://user:password@localhost:5432/search_db"


def init_db():
    url = get_db_url()
    try:
        engine = create_engine(url)
        # Create tables if they don't exist
        Base.metadata.create_all(bind=engine)
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)
    except Exception as e:
        logger.error(f"Failed to connect to DB at {url}: {e}")
        raise
