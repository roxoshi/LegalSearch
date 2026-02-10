import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import models from backend if in pythonpath, or redefine base
# To ensure standalone capability without brittle relative imports if backend moves,
# we might want to ensure backend is accessible or copy the model definition.
# For now, assuming PYTHONPATH will include project root.

try:
    from backend.app.models import Base, Document
except ImportError:
    from app.models import Base, Document  # type: ignore[no-redef]  # noqa: F401

logger = logging.getLogger(__name__)


def get_db_url():
    # Allow overriding via env var, default to localhost for local run
    return os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/search_db")


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
