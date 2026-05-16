"""
Tests for etl/ingest_legal_library.py.

Dry-run tests verify record counts and cross-reference parsing without
touching any database — they call the ingest functions with dry_run=True
and a None session. DB-write tests are skipped when PostgreSQL is unavailable.
"""

import json
import pathlib
import re
import tempfile

import pytest

DATA_DIR = pathlib.Path(__file__).parent.parent.parent / ".data"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _count_json_records(directory: pathlib.Path) -> int:
    total = 0
    for f in directory.glob("*.json"):
        total += len(json.loads(f.read_text(encoding="utf-8")))
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run record count tests
# ─────────────────────────────────────────────────────────────────────────────

def test_ingest_acts_dry_run_count():
    """ingest_acts() in dry-run mode counts exactly as many records as the JSON files contain."""
    pytest.importorskip("etl.ingest_legal_library")
    from etl.ingest_legal_library import ingest_acts

    expected = _count_json_records(DATA_DIR / "acts")
    assert expected > 0, "No act JSON files found — check DATA_DIR"

    count = ingest_acts(session=None, data_dir=DATA_DIR, dry_run=True)
    assert count == expected, f"Expected {expected} acts, got {count}"


def test_ingest_rules_dry_run_count():
    """ingest_rules() dry-run count matches source JSON files."""
    pytest.importorskip("etl.ingest_legal_library")
    from etl.ingest_legal_library import ingest_rules

    expected = _count_json_records(DATA_DIR / "rules")
    assert expected > 0

    count = ingest_rules(session=None, data_dir=DATA_DIR, dry_run=True)
    assert count == expected


def test_ingest_notifications_dry_run_count():
    """ingest_notifications() dry-run count matches source JSON files."""
    pytest.importorskip("etl.ingest_legal_library")
    from etl.ingest_legal_library import ingest_notifications

    expected = _count_json_records(DATA_DIR / "notifications" / "jsons")
    assert expected > 0

    count = ingest_notifications(session=None, data_dir=DATA_DIR, dry_run=True)
    assert count == expected


def test_ingest_circulars_dry_run_count():
    """ingest_circulars() dry-run count matches source JSON files."""
    pytest.importorskip("etl.ingest_legal_library")
    from etl.ingest_legal_library import ingest_circulars

    expected = _count_json_records(DATA_DIR / "circulars" / "jsons")
    assert expected > 0

    count = ingest_circulars(session=None, data_dir=DATA_DIR, dry_run=True)
    assert count == expected


def test_ingest_cross_references_dry_run_nonzero():
    """ingest_cross_references() finds cross-reference links in acts/rules HTML."""
    pytest.importorskip("etl.ingest_legal_library")
    from etl.ingest_legal_library import ingest_cross_references

    count = ingest_cross_references(session=None, data_dir=DATA_DIR, dry_run=True)
    assert count > 0, "Expected at least one cross-reference to be parsed"


# ─────────────────────────────────────────────────────────────────────────────
# Cross-reference parsing unit tests (no DB, no files needed)
# ─────────────────────────────────────────────────────────────────────────────

_XREF_RE = re.compile(r"explore-(notification|rule|act|circular)/(\d+)", re.IGNORECASE)


def test_xref_regex_matches_notification_link():
    html = '<a href="https://example.com/content-page/explore-notification/1000868">Section 2(61)</a>'
    matches = _XREF_RE.findall(html)
    assert len(matches) == 1
    assert matches[0] == ("notification", "1000868")


def test_xref_regex_matches_multiple_types():
    html = (
        'See <a href="/explore-rule/2001">Rule 1</a> and '
        '<a href="/explore-circular/3001">Circular 1</a>.'
    )
    matches = _XREF_RE.findall(html)
    assert len(matches) == 2
    types = {m[0] for m in matches}
    assert types == {"rule", "circular"}


def test_xref_regex_no_false_positives():
    html = "<p>explore-nothing/999 is not a valid link</p>"
    matches = _XREF_RE.findall(html)
    assert len(matches) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Field normalisation tests
# ─────────────────────────────────────────────────────────────────────────────

def test_acts_have_required_fields():
    """Every act record must have primary_id, content_id, act_name, section_no."""
    required = {"primary_id", "content_id", "act_name", "section_no"}
    for f in (DATA_DIR / "acts").glob("*.json"):
        for i, rec in enumerate(json.loads(f.read_text())):
            missing = required - rec.keys()
            assert not missing, f"{f.name}[{i}] missing fields: {missing}"


def test_notifications_have_required_fields():
    """Every notification record must have primary_id, content_id, notification_no."""
    required = {"primary_id", "content_id", "notification_no"}
    for f in (DATA_DIR / "notifications" / "jsons").glob("*.json"):
        for i, rec in enumerate(json.loads(f.read_text())):
            missing = required - rec.keys()
            assert not missing, f"{f.name}[{i}] missing fields: {missing}"


def test_circulars_have_required_fields():
    """Every circular record must have primary_id, content_id, circular_no."""
    required = {"primary_id", "content_id", "circular_no"}
    for f in (DATA_DIR / "circulars" / "jsons").glob("*.json"):
        for i, rec in enumerate(json.loads(f.read_text())):
            missing = required - rec.keys()
            assert not missing, f"{f.name}[{i}] missing fields: {missing}"


def test_all_primary_ids_are_integers():
    """primary_id must be an integer (not a string) in all four document types."""
    dirs = [
        DATA_DIR / "acts",
        DATA_DIR / "rules",
        DATA_DIR / "notifications" / "jsons",
        DATA_DIR / "circulars" / "jsons",
    ]
    for d in dirs:
        for f in d.glob("*.json"):
            for i, rec in enumerate(json.loads(f.read_text())):
                assert isinstance(rec.get("primary_id"), int), (
                    f"{f.name}[{i}] primary_id is not int: {rec.get('primary_id')!r}"
                )


def test_content_ids_are_integers_when_present():
    """content_id must be an integer when present; None values are a known data quality
    issue in some notification source files (those records are skipped at ingest time
    because the NOT NULL DB constraint rejects them).
    """
    dirs = [
        DATA_DIR / "acts",
        DATA_DIR / "rules",
        DATA_DIR / "notifications" / "jsons",
        DATA_DIR / "circulars" / "jsons",
    ]
    for d in dirs:
        for f in d.glob("*.json"):
            for i, rec in enumerate(json.loads(f.read_text())):
                cid = rec.get("content_id")
                if cid is None:
                    continue  # known data quality issue; ingestion skips these
                assert isinstance(cid, int), (
                    f"{f.name}[{i}] content_id is not int: {cid!r}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Missing data-dir graceful handling
# ─────────────────────────────────────────────────────────────────────────────

def test_ingest_acts_missing_dir_returns_zero():
    """ingest_acts() returns 0 and logs a warning when the acts/ directory is absent."""
    pytest.importorskip("etl.ingest_legal_library")
    from etl.ingest_legal_library import ingest_acts

    with tempfile.TemporaryDirectory() as tmpdir:
        count = ingest_acts(session=None, data_dir=pathlib.Path(tmpdir), dry_run=True)
    assert count == 0
