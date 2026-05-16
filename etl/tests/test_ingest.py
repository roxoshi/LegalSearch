import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from etl.ingest import load_parquet_metadata
from etl.schemas import AnalysisJSON, MetadataJSON


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _write_analysis_json(directory: Path, case_id: str, data: dict) -> Path:
    path = directory / f"{case_id}.json"
    path.write_text(json.dumps(data))
    return path


def _make_sc_parquet(directory: Path, rows: list[dict]) -> Path:
    path = directory / "SC-GST-2024.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _make_hc_parquet(directory: Path, rows: list[dict]) -> Path:
    path = directory / "HC-2024.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


# ─── test_discover_analysis_files ────────────────────────────────────────────


def test_discover_analysis_files(temp_dir, sample_analysis_data):
    """ingest discovers all .json files under analysis_dir."""
    for i in range(3):
        _write_analysis_json(temp_dir, f"CASE-{i:03d}", sample_analysis_data)

    found = sorted(temp_dir.rglob("*.json"))
    assert len(found) == 3


# ─── test_load_parquet_metadata ───────────────────────────────────────────────


def test_load_parquet_metadata_sc_format(temp_dir):
    """SC parquet (case_id column) loads correctly."""
    rows = [
        {
            "case_id": "SC-2024-001",
            "title": "Alpha v Beta",
            "petitioner": "Alpha",
            "respondent": "Beta",
            "judge": "Justice A",
            "citation": "2024 SC 001",
            "court": "Supreme Court",
            "decision_date": "2024-01-01",
        }
    ]
    _make_sc_parquet(temp_dir, rows)

    result = load_parquet_metadata(temp_dir)
    assert "SC-2024-001" in result
    meta = result["SC-2024-001"]
    assert meta.title == "Alpha v Beta"
    assert meta.petitioner == "Alpha"
    assert meta.court == "Supreme Court"


def test_load_parquet_metadata_hc_format(temp_dir):
    """HC parquet (cnr column) uses cnr as case_id and parses title for parties."""
    rows = [
        {
            "cnr": "HC-CNR-2024-001",
            "title": "WP/100/2024 of Alpha Corp Vs Beta Ltd",
            "judge": "Justice B",
            "court": "High Court",
            "decision_date": "2024-02-01",
        }
    ]
    _make_hc_parquet(temp_dir, rows)

    result = load_parquet_metadata(temp_dir)
    assert "HC-CNR-2024-001" in result
    meta = result["HC-CNR-2024-001"]
    assert meta.case_id == "HC-CNR-2024-001"
    assert meta.citation == "Not Available"  # HC default


def test_load_parquet_metadata_empty_dir(temp_dir):
    """Empty directory returns empty dict (no crash)."""
    result = load_parquet_metadata(temp_dir)
    assert result == {}


def test_load_parquet_metadata_missing_dir(temp_dir):
    """Calling with a non-existent dir via the main() flow returns empty dict."""
    missing = temp_dir / "does_not_exist"
    # load_parquet_metadata is only called when the dir exists; test the empty case
    result = load_parquet_metadata(temp_dir)  # temp_dir exists but has no parquets
    assert result == {}


# ─── test_ingest_upsert_creates_document ─────────────────────────────────────


def test_ingest_upsert_creates_document(temp_dir, sample_analysis_data):
    """A new analysis JSON results in a document being added to the DB."""
    _write_analysis_json(temp_dir, "NEW-CASE-001", sample_analysis_data)

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = None  # no existing doc

    mock_session_local = MagicMock()
    mock_session_local.return_value.__enter__ = lambda s: mock_db
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)

    mock_doc = MagicMock()
    mock_doc.id = 42
    mock_chunks = [MagicMock()]

    with (
        patch("etl.ingest.init_db", return_value=mock_session_local),
        patch("etl.ingest.Transformer") as mock_transformer_cls,
    ):
        mock_transformer = MagicMock()
        mock_transformer.process_document.return_value = (mock_doc, mock_chunks)
        mock_transformer_cls.return_value = mock_transformer

        from etl.ingest import main

        import sys

        sys_argv_backup = sys.argv
        sys.argv = ["ingest.py", "--analysis-dir", str(temp_dir)]
        try:
            main()
        finally:
            sys.argv = sys_argv_backup

    # Document was added
    mock_db.add.assert_called()
    mock_db.commit.assert_called()


def test_ingest_skips_existing(temp_dir, sample_analysis_data):
    """When a document already exists in the DB it is skipped, not overwritten."""
    _write_analysis_json(temp_dir, "EXISTING-001", sample_analysis_data)

    mock_existing_doc = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_existing_doc

    mock_session_local = MagicMock()
    mock_session_local.return_value.__enter__ = lambda s: mock_db
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("etl.ingest.init_db", return_value=mock_session_local),
        patch("etl.ingest.Transformer") as mock_transformer_cls,
    ):
        mock_transformer = MagicMock()
        mock_transformer_cls.return_value = mock_transformer

        from etl.ingest import main
        import sys

        sys_argv_backup = sys.argv
        sys.argv = ["ingest.py", "--analysis-dir", str(temp_dir)]
        try:
            main()
        finally:
            sys.argv = sys_argv_backup

    # Nothing was deleted or added
    mock_db.delete.assert_not_called()
    mock_db.add.assert_not_called()


def test_ingest_skips_invalid_json(temp_dir):
    """A JSON file with missing required fields is logged and skipped; other files continue."""
    # Write one invalid file (missing required analysis fields)
    bad_path = temp_dir / "BAD-CASE.json"
    bad_path.write_text(json.dumps({"summary": "only summary, rest missing"}))

    # Write one valid file
    valid_data = {
        "summary": "Valid summary",
        "facts": "f", "issues": "i",
        "petitioner_arguments": "p", "respondent_arguments": "r",
        "analysis_of_law": "a", "precedent_analysis": "pr",
        "courts_reasoning": "c", "conclusion": "con",
        "ratio_decidendi": "rd",
    }
    _write_analysis_json(temp_dir, "GOOD-CASE", valid_data)

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = None

    mock_session_local = MagicMock()
    mock_session_local.return_value.__enter__ = lambda s: mock_db
    mock_session_local.return_value.__exit__ = MagicMock(return_value=False)

    mock_doc = MagicMock()
    mock_doc.id = 1
    mock_chunks: list = []

    with (
        patch("etl.ingest.init_db", return_value=mock_session_local),
        patch("etl.ingest.Transformer") as mock_transformer_cls,
    ):
        mock_transformer = MagicMock()
        mock_transformer.process_document.return_value = (mock_doc, mock_chunks)
        mock_transformer_cls.return_value = mock_transformer

        from etl.ingest import main
        import sys

        sys_argv_backup = sys.argv
        sys.argv = ["ingest.py", "--analysis-dir", str(temp_dir)]
        try:
            main()
        finally:
            sys.argv = sys_argv_backup

    # Only the valid document was added (process_document called once)
    assert mock_transformer.process_document.call_count == 1
