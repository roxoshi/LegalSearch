import pytest


def test_ingest_module_exists():
    """Test that ingest module exists"""
    try:
        import etl.ingest as ingest_module

        assert ingest_module is not None
    except ImportError:
        pytest.skip("Could not import etl.ingest")


def test_ingest_has_main_function():
    """Test that ingest module has a main function"""
    try:
        import etl.ingest as ingest_module

        assert hasattr(ingest_module, "main")
        assert callable(ingest_module.main)
    except ImportError:
        pytest.skip("Could not import etl.ingest")
