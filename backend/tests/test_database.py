from app.database import Base, engine, get_database


def test_get_database():
    """Test the get_database dependency"""
    db_gen = get_database()
    db = next(db_gen)

    assert db is not None

    # Clean up
    try:
        next(db_gen)
    except StopIteration:
        pass  # Expected


def test_engine_exists():
    """Test that engine is created"""
    assert engine is not None


def test_base_metadata():
    """Test that Base has metadata"""
    assert Base.metadata is not None
    assert hasattr(Base.metadata, "create_all")
    assert hasattr(Base.metadata, "drop_all")
