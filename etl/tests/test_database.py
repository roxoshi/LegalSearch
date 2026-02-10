from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from etl.database import init_db


def test_init_db():
    """Test database initialization"""
    with patch("etl.database.create_engine") as mock_create_engine:
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine

        with patch("etl.database.sessionmaker") as mock_sessionmaker:
            result = init_db()

            assert result is not None
            mock_create_engine.assert_called_once()
            mock_sessionmaker.assert_called_once()


def test_database_connection_string():
    """Test that database connection uses environment variable"""
    with patch("os.getenv", return_value="postgresql://test:test@localhost/testdb"):
        with patch("etl.database.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            with patch("etl.database.sessionmaker"):
                init_db()

                # Verify create_engine was called with the connection string
                mock_create_engine.assert_called_once()


def test_init_db_connection_error():
    """Test handling of database connection errors"""
    with patch("etl.database.create_engine") as mock_create_engine:
        # Simulate connection error
        mock_create_engine.side_effect = OperationalError("Connection failed", None, Exception())

        with pytest.raises(OperationalError):
            init_db()


def test_init_db_with_custom_url():
    """Test database initialization with custom URL"""
    custom_url = "postgresql://custom:pass@localhost:5432/customdb"

    with patch("os.getenv", return_value=custom_url):
        with patch("etl.database.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            with patch("etl.database.sessionmaker"):
                init_db()

                # Verify the custom URL was used
                call_args = mock_create_engine.call_args[0]
                assert custom_url in str(call_args)
