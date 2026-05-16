import pytest

from etl.schemas import ANALYSIS_FIELD_LABELS, AnalysisJSON, MetadataJSON


# ─── ANALYSIS_FIELD_LABELS ────────────────────────────────────────────────────


def test_analysis_field_labels_has_ten_entries():
    assert len(ANALYSIS_FIELD_LABELS) == 10


def test_analysis_field_labels_keys_match_analysis_json_fields():
    fields = set(AnalysisJSON.model_fields.keys())
    assert set(ANALYSIS_FIELD_LABELS.keys()) == fields


# ─── AnalysisJSON ─────────────────────────────────────────────────────────────


def test_analysis_json_validates_valid_data(sample_analysis_data):
    analysis = AnalysisJSON(**sample_analysis_data)
    assert analysis.summary == sample_analysis_data["summary"]
    assert analysis.ratio_decidendi == sample_analysis_data["ratio_decidendi"]


def test_analysis_json_requires_all_ten_fields(sample_analysis_data):
    """Missing any required field should raise a validation error."""
    for field_name in ANALYSIS_FIELD_LABELS:
        incomplete = {k: v for k, v in sample_analysis_data.items() if k != field_name}
        with pytest.raises(Exception):
            AnalysisJSON(**incomplete)


def test_analysis_json_ignores_extra_fields(sample_analysis_data):
    data = {**sample_analysis_data, "unknown_field": "should be ignored"}
    analysis = AnalysisJSON(**data)
    assert not hasattr(analysis, "unknown_field")


# ─── MetadataJSON ─────────────────────────────────────────────────────────────


def test_metadata_json_all_defaults():
    """MetadataJSON should be constructible with no arguments."""
    meta = MetadataJSON()
    assert meta.case_id == ""
    assert meta.title == ""
    assert meta.petitioner == "Unknown"
    assert meta.respondent == "Unknown"
    assert meta.judge == "Unknown"
    assert meta.citation == "Unknown"
    assert meta.court == "Unknown Court"
    assert meta.decision_date == ""


def test_metadata_json_full_data(sample_metadata_data):
    meta = MetadataJSON(**sample_metadata_data)
    assert meta.case_id == "TEST-2024-001"
    assert meta.title == "Test Petitioner v. Test Respondent"
    assert meta.court == "Supreme Court of India"


def test_metadata_json_ignores_extra_fields(sample_metadata_data):
    data = {**sample_metadata_data, "extra_key": "ignored"}
    meta = MetadataJSON(**data)
    assert not hasattr(meta, "extra_key")
