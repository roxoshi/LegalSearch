"""
Tests for the High Court (HC) extraction module.

Tests cover:
- Title parsing (petitioner/respondent extraction)
- HC record normalization to common schema
- HC metadata loading from parquet files
- Tar path resolution
- Single record extraction from tar archives
- Parallel extraction
"""

import io
import tarfile
from unittest.mock import patch

import pandas as pd
import pytest

from pipelines.shard_hc import (
    _extract_single_hc_record,
    _extract_tar_batch,
    _resolve_tar_path,
    load_hc_metadata,
    normalize_hc_record,
    parallel_extract_from_tars,
    parse_title_parties,
)

# ============================================================================
# Test parse_title_parties
# ============================================================================


class TestParseTitleParties:
    def test_standard_vs_format(self):
        pet, resp = parse_title_parties("RFA/924/2012 of NTPC LTD Vs SUNDER")
        assert pet == "NTPC LTD"
        assert resp == "SUNDER"

    def test_lowercase_vs(self):
        pet, resp = parse_title_parties("WP/123/2020 of ABC Corp vs State of Gujarat")
        assert pet == "ABC Corp"
        assert resp == "State of Gujarat"

    def test_uppercase_vs(self):
        pet, resp = parse_title_parties("CMP/456/2019 of KUMAR VS SINGH")
        assert pet == "KUMAR"
        assert resp == "SINGH"

    def test_v_slash_s(self):
        pet, resp = parse_title_parties("CMP/456/2019 of KUMAR v/s SINGH")
        assert pet == "KUMAR"
        assert resp == "SINGH"

    def test_v_dot(self):
        pet, resp = parse_title_parties("CMP/456/2019 of KUMAR v. SINGH")
        assert pet == "KUMAR"
        assert resp == "SINGH"

    def test_no_of_keyword(self):
        """Title without 'of' should still attempt splitting on Vs."""
        pet, resp = parse_title_parties("NTPC LTD Vs SUNDER")
        assert pet == "NTPC LTD"
        assert resp == "SUNDER"

    def test_empty_title(self):
        assert parse_title_parties("") == ("Unknown", "Unknown")

    def test_none_title(self):
        assert parse_title_parties(None) == ("Unknown", "Unknown")

    def test_no_vs_separator(self):
        assert parse_title_parties("Some random title without parties") == ("Unknown", "Unknown")

    def test_multiple_vs_takes_first_split(self):
        pet, resp = parse_title_parties("A/1/2020 of X Vs Y Vs Z")
        assert pet == "X"
        assert resp == "Y Vs Z"


# ============================================================================
# Test normalize_hc_record
# ============================================================================


class TestNormalizeHcRecord:
    def test_basic_normalization(self):
        record = {
            "court_code": "2~5",
            "title": "RFA/924/2012 of NTPC LTD Vs SUNDER",
            "judge": "JUSTICE KAROL",
            "pdf_link": "court/cnrorders/cmis/orders/HPHC010168032012_1_2018-03-07.pdf",
            "cnr": "HPHC010168032012",
            "decision_date": pd.Timestamp("2018-03-07"),
            "disposal_nature": "Allowed",
            "court": "High Court of Himachal Pradesh",
        }
        result = normalize_hc_record(record)

        assert result["case_id"] == "HPHC010168032012"
        assert result["citation"] == "HPHC010168032012"
        assert result["petitioner"] == "NTPC LTD"
        assert result["respondent"] == "SUNDER"
        assert result["year"] == "2018"
        assert result["path"] == "HPHC010168032012_1_2018-03-07.pdf"
        assert result["court_code"] == "2~5"
        assert result["court"] == "High Court of Himachal Pradesh"
        assert result["decision_date"] == "2018-03-07"
        assert result["disposal_nature"] == "Allowed"
        assert result["_source"] == "hc"

    def test_string_decision_date(self):
        record = {
            "cnr": "TEST123",
            "title": "",
            "decision_date": "2019-05-15",
            "court_code": "",
            "pdf_link": "",
            "judge": "",
            "court": "",
            "disposal_nature": "",
        }
        result = normalize_hc_record(record)
        assert result["year"] == "2019"

    def test_missing_fields_have_defaults(self):
        record = {"cnr": "X123"}
        result = normalize_hc_record(record)
        assert result["case_id"] == "X123"
        assert result["petitioner"] == "Unknown"
        assert result["respondent"] == "Unknown"
        assert result["judge"] == "Unknown"
        assert result["court"] == "Unknown Court"


# ============================================================================
# Test load_hc_metadata
# ============================================================================


class TestLoadHcMetadata:
    @pytest.fixture
    def metadata_dir(self, tmp_path):
        """Create a temp dir with mock HC parquet files."""
        data = {
            "court_code": ["2~5", "1~12"],
            "title": ["A Vs B", "C Vs D"],
            "judge": ["Judge1", "Judge2"],
            "pdf_link": [
                "court/cnrorders/cmis/orders/F1.pdf",
                "court/cnrorders/jammuhc/orders/F2.pdf",
            ],
            "cnr": ["CNR001", "CNR002"],
            "decision_date": [pd.Timestamp("2018-06-01"), pd.Timestamp("2018-07-01")],
            "disposal_nature": ["", "Dismissed"],
            "court": ["HC Himachal", "HC J&K"],
        }
        df = pd.DataFrame(data)
        df.to_parquet(tmp_path / "HC-GST-2018.parquet", index=False)

        # Also create an SC file to ensure it's not loaded
        sc_data = {
            "title": ["SC Case"],
            "petitioner": ["P"],
            "respondent": ["R"],
            "judge": ["J"],
            "citation": ["C"],
            "case_id": ["SC1"],
            "cnr": ["SCN1"],
            "decision_date": ["2018-01-01"],
            "disposal_nature": [""],
            "court": ["SC"],
            "nc_display": [""],
            "year": ["2018"],
            "path": ["p.pdf"],
        }
        pd.DataFrame(sc_data).to_parquet(tmp_path / "SC-GST-2018.parquet", index=False)

        return str(tmp_path)

    def test_loads_only_hc_files(self, metadata_dir):
        records = load_hc_metadata(metadata_dir)
        assert len(records) == 2
        assert all(r["_source"] == "hc" for r in records)

    def test_year_filtering(self, metadata_dir):
        # No HC-GST-2019 exists, so filtering to 2019 should return empty
        records = load_hc_metadata(metadata_dir, years=[2019])
        assert len(records) == 0

        # Filtering to 2018 should return records
        records = load_hc_metadata(metadata_dir, years=[2018])
        assert len(records) == 2

    def test_deduplicates_by_cnr(self, tmp_path):
        """Duplicate CNRs should be deduplicated."""
        data = {
            "court_code": ["2~5", "2~5"],
            "title": ["A Vs B", "A Vs B"],
            "judge": ["J", "J"],
            "pdf_link": ["p1.pdf", "p2.pdf"],
            "cnr": ["SAME_CNR", "SAME_CNR"],
            "decision_date": [pd.Timestamp("2018-06-01"), pd.Timestamp("2018-07-01")],
            "disposal_nature": ["", ""],
            "court": ["HC", "HC"],
        }
        pd.DataFrame(data).to_parquet(tmp_path / "HC-GST-2018.parquet", index=False)

        records = load_hc_metadata(str(tmp_path))
        assert len(records) == 1

    def test_empty_dir(self, tmp_path):
        records = load_hc_metadata(str(tmp_path))
        assert records == []


# ============================================================================
# Test _resolve_tar_path
# ============================================================================


class TestResolveTarPath:
    def test_standard_path(self):
        record = {
            "year": "2018",
            "court_code": "2~5",
            "pdf_link": "court/cnrorders/cmis/orders/HPHC010168032012_1_2018-03-07.pdf",
        }
        tar_path, bench, pdf_filename = _resolve_tar_path("/data/judgments", record)

        assert tar_path == "/data/judgments/HC-GST-2018/court=2_5/bench=cmis/data.tar"
        assert bench == "cmis"
        assert pdf_filename == "HPHC010168032012_1_2018-03-07.pdf"

    def test_court_code_tilde_to_underscore(self):
        record = {
            "year": "2018",
            "court_code": "27~1",
            "pdf_link": "court/cnrorders/kolhcdb/orders/FILE.pdf",
        }
        tar_path, _, _ = _resolve_tar_path("/base", record)
        assert "court=27_1" in tar_path

    def test_empty_pdf_link(self):
        record = {"year": "2018", "court_code": "2~5", "pdf_link": ""}
        tar_path, bench, pdf_filename = _resolve_tar_path("/base", record)
        assert bench == ""
        assert pdf_filename == ""


# ============================================================================
# Test _extract_single_hc_record
# ============================================================================


class TestExtractSingleHcRecord:
    @pytest.fixture
    def tar_with_pdf(self, tmp_path):
        """Create a temp tar file with a dummy PDF."""
        # Minimal valid PDF
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"

        tar_dir = tmp_path / "HC-GST-2018" / "court=2_5" / "bench=cmis"
        tar_dir.mkdir(parents=True)
        tar_path = tar_dir / "data.tar"

        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name="./TEST_FILE.pdf")
            info.size = len(pdf_content)
            tf.addfile(info, io.BytesIO(pdf_content))

        return str(tmp_path)

    def test_extracts_pdf_from_tar(self, tar_with_pdf):
        record = {
            "case_id": "TEST123",
            "title": "Test Case",
            "petitioner": "A",
            "respondent": "B",
            "judge": "Judge",
            "citation": "TEST123",
            "court": "Test Court",
            "decision_date": "2018-01-01",
            "year": "2018",
            "path": "TEST_FILE.pdf",
            "court_code": "2~5",
            "pdf_link": "court/cnrorders/cmis/orders/TEST_FILE.pdf",
        }

        with patch("pipelines.shard_hc.extract_text_pymupdf", return_value="extracted text"):
            result = _extract_single_hc_record((record, tar_with_pdf))

        assert result is not None
        assert result.case_id == "TEST123"
        assert result.text_content == "extracted text"
        assert result.pdf_bytes is not None

    def test_returns_none_for_missing_tar(self, tmp_path):
        record = {
            "case_id": "MISSING",
            "year": "2099",
            "court_code": "99~99",
            "pdf_link": "court/cnrorders/bench/orders/X.pdf",
            "path": "X.pdf",
        }
        result = _extract_single_hc_record((record, str(tmp_path)))
        assert result is None

    def test_returns_none_for_missing_file_in_tar(self, tar_with_pdf):
        record = {
            "case_id": "NOTHERE",
            "year": "2018",
            "court_code": "2~5",
            "pdf_link": "court/cnrorders/cmis/orders/NONEXISTENT.pdf",
            "path": "NONEXISTENT.pdf",
        }
        result = _extract_single_hc_record((record, tar_with_pdf))
        assert result is None


# ============================================================================
# Test _extract_tar_batch
# ============================================================================


class TestExtractTarBatch:
    @pytest.fixture
    def tar_with_two_pdfs(self, tmp_path):
        """Create a tar with two dummy PDFs."""
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"

        tar_dir = tmp_path / "HC-GST-2018" / "court=2_5" / "bench=cmis"
        tar_dir.mkdir(parents=True)
        tar_path = tar_dir / "data.tar"

        with tarfile.open(tar_path, "w") as tf:
            for name in ["./FILE_A.pdf", "./FILE_B.pdf"]:
                info = tarfile.TarInfo(name=name)
                info.size = len(pdf_content)
                tf.addfile(info, io.BytesIO(pdf_content))

        return str(tar_path)

    @pytest.fixture
    def staging_dir(self, tmp_path):
        """Create a staging directory for PDFs."""
        d = tmp_path / "staging"
        d.mkdir()
        return str(d)

    def test_batch_extracts_multiple_from_one_tar(self, tar_with_two_pdfs, staging_dir):
        records = [
            {
                "case_id": "A1",
                "title": "Test A",
                "petitioner": "PA",
                "respondent": "RA",
                "judge": "J",
                "citation": "A1",
                "court": "HC",
                "decision_date": "2018-01-01",
                "year": "2018",
                "path": "FILE_A.pdf",
                "court_code": "2~5",
                "pdf_link": "court/cnrorders/cmis/orders/FILE_A.pdf",
            },
            {
                "case_id": "B1",
                "title": "Test B",
                "petitioner": "PB",
                "respondent": "RB",
                "judge": "J",
                "citation": "B1",
                "court": "HC",
                "decision_date": "2018-01-01",
                "year": "2018",
                "path": "FILE_B.pdf",
                "court_code": "2~5",
                "pdf_link": "court/cnrorders/cmis/orders/FILE_B.pdf",
            },
        ]

        with patch("pipelines.shard_hc.extract_text_pymupdf", return_value="text"):
            results, errors = _extract_tar_batch((tar_with_two_pdfs, records, staging_dir))

        assert len(results) == 2
        assert results[0] is not None
        assert results[1] is not None
        assert results[0].case_id == "A1"
        assert results[1].case_id == "B1"
        # pdf_bytes should be None (written to staging instead)
        assert results[0].pdf_bytes is None
        assert results[0].pdf_staging_path is not None

    def test_batch_returns_none_for_missing_files(self, tar_with_two_pdfs, staging_dir):
        records = [
            {
                "case_id": "X1",
                "pdf_link": "court/cnrorders/cmis/orders/NONEXISTENT.pdf",
                "path": "NONEXISTENT.pdf",
            },
        ]

        with patch("pipelines.shard_hc.extract_text_pymupdf", return_value="text"):
            results, errors = _extract_tar_batch((tar_with_two_pdfs, records, staging_dir))

        assert len(results) == 1
        assert results[0] is None
        assert len(errors) == 1
        assert errors[0]["pdf"] == "NONEXISTENT.pdf"

    def test_batch_returns_none_list_for_missing_tar(self, staging_dir):
        records = [{"case_id": "X", "pdf_link": "a/b.pdf"}]
        results, errors = _extract_tar_batch(("/nonexistent/data.tar", records, staging_dir))
        assert len(results) == 1
        assert results[0] is None
        assert len(errors) == 1
        assert "not found" in errors[0]["warnings"]

    def test_batch_logs_empty_text(self, tar_with_two_pdfs, staging_dir):
        """PDFs that produce empty text are recorded in errors."""
        records = [
            {
                "case_id": "EMPTY1",
                "title": "Empty",
                "petitioner": "P",
                "respondent": "R",
                "judge": "J",
                "citation": "EMPTY1",
                "court": "HC",
                "decision_date": "2018-01-01",
                "year": "2018",
                "path": "FILE_A.pdf",
                "court_code": "2~5",
                "pdf_link": "court/cnrorders/cmis/orders/FILE_A.pdf",
            },
        ]

        with patch("pipelines.shard_hc.extract_text_pymupdf", return_value=""):
            results, errors = _extract_tar_batch((tar_with_two_pdfs, records, staging_dir))

        # Document is still created (with empty text), but error is logged
        assert len(results) == 1
        assert results[0] is not None
        assert results[0].text_content == ""
        assert len(errors) == 1
        assert errors[0]["empty_text"] is True
        assert errors[0]["pdf"] == "FILE_A.pdf"


# ============================================================================
# Test parallel_extract_from_tars
# ============================================================================


class TestParallelExtractFromTars:
    def test_parallel_extraction_returns_docs(self, tmp_path):
        """Test parallel extraction with a real small tar."""
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"

        tar_dir = tmp_path / "HC-GST-2018" / "court=2_5" / "bench=cmis"
        tar_dir.mkdir(parents=True)
        tar_path = tar_dir / "data.tar"

        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name="./TEST_PAR.pdf")
            info.size = len(pdf_content)
            tf.addfile(info, io.BytesIO(pdf_content))

        staging = tmp_path / "staging"
        records = [
            {
                "case_id": "PAR1",
                "title": "Test",
                "petitioner": "A",
                "respondent": "B",
                "judge": "J",
                "citation": "PAR1",
                "court": "HC",
                "decision_date": "2018-01-01",
                "year": "2018",
                "path": "TEST_PAR.pdf",
                "court_code": "2~5",
                "pdf_link": "court/cnrorders/cmis/orders/TEST_PAR.pdf",
            },
        ]

        with patch("pipelines.shard_hc.extract_text_pymupdf", return_value="text"):
            results = parallel_extract_from_tars(
                records, str(tmp_path), staging_dir=str(staging)
            )

        assert len(results) == 1
        assert results[0].case_id == "PAR1"
        assert results[0].pdf_bytes is None
        assert results[0].pdf_staging_path is not None

    def test_filters_none_results(self, tmp_path):
        """Records pointing to nonexistent tars return None and are filtered."""
        staging = tmp_path / "staging"
        records = [
            {
                "case_id": "F1",
                "year": "2099",
                "court_code": "99~99",
                "pdf_link": "court/cnrorders/b/orders/F.pdf",
                "path": "F.pdf",
            },
        ]
        results = parallel_extract_from_tars(
            records, str(tmp_path), staging_dir=str(staging)
        )
        assert len(results) == 0

    def test_empty_records(self):
        results = parallel_extract_from_tars([], "/fake/dir")
        assert results == []

    def test_writes_error_log(self, tmp_path):
        """Records pointing to nonexistent tars produce an error log."""
        log_dir = tmp_path / "logs"
        staging = tmp_path / "staging"
        records = [
            {
                "case_id": "ERR1",
                "year": "2099",
                "court_code": "99~99",
                "pdf_link": "court/cnrorders/b/orders/F.pdf",
                "path": "F.pdf",
            },
        ]
        parallel_extract_from_tars(
            records, str(tmp_path), log_dir=str(log_dir), staging_dir=str(staging)
        )

        log_file = log_dir / "hc_extraction_errors.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "ERR1" in content
        assert "F.pdf" in content
