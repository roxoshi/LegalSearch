"""
PDF-to-HTML Conversion Fidelity Scoring

This module provides metrics to evaluate the quality of PDF-to-HTML conversion.
It compares the extracted text against heuristics for legal documents to detect:
- Fragmented text (abrupt line breaks mid-word/sentence)
- False heading detection (non-heading content marked as headings)
- OCR/extraction artifacts
- Missing or corrupted content

Usage:
    from pipelines.conversion_fidelity import score_conversion, FidelityReport

    report = score_conversion(html_content, pdf_path)
    print(f"Overall score: {report.overall_score}")
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup


@dataclass
class FidelityReport:
    """Report of conversion fidelity metrics."""

    # Core scores (0-100, higher is better)
    text_continuity_score: float = 100.0
    heading_accuracy_score: float = 100.0
    artifact_score: float = 100.0
    structure_score: float = 100.0

    # Issue counts
    fragmented_lines: int = 0
    false_headings: int = 0
    artifacts_found: int = 0
    margin_markers_leaked: int = 0

    # Detailed issues for debugging
    issues: list = field(default_factory=list)

    @property
    def overall_score(self) -> float:
        """Weighted average of all scores."""
        weights = {
            "text_continuity": 0.35,
            "heading_accuracy": 0.25,
            "artifact": 0.25,
            "structure": 0.15,
        }
        return (
            self.text_continuity_score * weights["text_continuity"]
            + self.heading_accuracy_score * weights["heading_accuracy"]
            + self.artifact_score * weights["artifact"]
            + self.structure_score * weights["structure"]
        )

    @property
    def passed(self) -> bool:
        """Whether the conversion meets minimum quality threshold."""
        return self.overall_score >= 70.0


# Patterns indicating text extraction problems
FRAGMENTATION_PATTERNS = [
    # Merged words (lowercase followed by uppercase mid-word, but not acronyms)
    re.compile(r"[a-z][A-Z][a-z]{2,}"),
    # Number merged with word
    re.compile(r"of\d{4}"),  # e.g., "of2016"
    re.compile(r"\d{4}of"),
    # Hyphenated word break artifacts (hyphen followed by space then lowercase)
    re.compile(r"\w-\s+[a-z]"),
    # Words broken by newline (word fragment ending in consonant cluster)
    re.compile(r"\b[bcdfghjklmnpqrstvwxz]{2,3}\s*$", re.MULTILINE),
]

# OCR/extraction artifacts common in legal PDFs
ARTIFACT_PATTERNS = [
    re.compile(r"[''`]s\b"),  # Curly apostrophe variants
    re.compile(r"\b[A-H]\b(?=\s+[a-z])"),  # Single letter margin markers before text
    re.compile(r"\.~"),  # OCR artifact
    re.compile(r"n'o\b"),  # Common OCR error for "no"
    re.compile(r"injw:v"),  # OCR garbage
    re.compile(r"\b\w+\.\.\w+\b"),  # Merged sentences
]

# Patterns that should NOT be headings in legal documents
FALSE_HEADING_PATTERNS = [
    # Judge names in brackets
    re.compile(r"^\[.*(?:JJ?\.|JUSTICE|Judge).*\]$", re.IGNORECASE),
    # Date patterns
    re.compile(
        r"^(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d",
        re.IGNORECASE,
    ),
    # Citation patterns
    re.compile(r"^\(.*Appeal.*\d+.*\)$", re.IGNORECASE),
    # Very short "headings" that are likely artifacts
    re.compile(r"^[A-Z]{1,2}$"),
    # Party names (v. pattern)
    re.compile(r"\bv\.\s*$", re.IGNORECASE),
]

# Legitimate heading patterns for legal documents
VALID_HEADING_KEYWORDS = {
    "judgment",
    "order",
    "headnote",
    "factual matrix",
    "facts",
    "issue",
    "issues for consideration",
    "case law cited",
    "appearances",
    "list of acts",
    "list of keywords",
    "case arising from",
    "books and periodicals",
    "conclusion",
    "arguments",
    "submissions",
    "analysis",
    "discussion",
    "ratio decidendi",
    "obiter dicta",
    "operative part",
}


def _extract_text_blocks(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Extract (tag_name, text) pairs from HTML."""
    blocks: list[tuple[str, str]] = []
    if soup.body is None:
        return blocks
    for elem in soup.body.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
        text = elem.get_text(separator=" ", strip=True)
        if text:
            blocks.append((elem.name, text))
    return blocks


def _score_text_continuity(blocks: list[tuple[str, str]]) -> tuple[float, int, list[str]]:
    """Score how well text flows without fragmentation."""
    issues = []
    fragmented_count = 0
    total_blocks = len(blocks)

    if total_blocks == 0:
        return 100.0, 0, []

    full_text = " ".join(text for _, text in blocks)

    for pattern in FRAGMENTATION_PATTERNS:
        matches = pattern.findall(full_text)
        if matches:
            fragmented_count += len(matches)
            issues.append(f"Fragmentation pattern '{pattern.pattern}': {len(matches)} matches")

    # Check for very short paragraphs that might indicate fragmentation
    short_paras = sum(1 for tag, text in blocks if tag == "p" and len(text.split()) < 4)
    if short_paras > total_blocks * 0.3:
        fragmented_count += short_paras
        issues.append(f"High ratio of short paragraphs: {short_paras}/{total_blocks}")

    # Score: penalize based on fragmentation density
    density = fragmented_count / max(1, total_blocks)
    score = max(0, 100 - (density * 100))

    return score, fragmented_count, issues


def _score_heading_accuracy(blocks: list[tuple[str, str]]) -> tuple[float, int, list[str]]:
    """Score accuracy of heading detection."""
    issues = []
    false_heading_count = 0

    headings = [(tag, text) for tag, text in blocks if tag.startswith("h")]

    if not headings:
        return 100.0, 0, []

    for _tag, text in headings:
        text_lower = text.lower().strip()

        # Check if it matches any false heading pattern
        is_false = any(p.search(text) for p in FALSE_HEADING_PATTERNS)

        # Check if it matches valid heading keywords
        is_valid = any(kw in text_lower for kw in VALID_HEADING_KEYWORDS)

        if is_false and not is_valid:
            false_heading_count += 1
            issues.append(f"False heading detected: '{text[:50]}...'")

    # Score based on false heading ratio
    if len(headings) > 0:
        accuracy = 1.0 - (false_heading_count / len(headings))
        score = accuracy * 100
    else:
        score = 100.0

    return score, false_heading_count, issues


def _score_artifacts(blocks: list[tuple[str, str]]) -> tuple[float, int, int, list[str]]:
    """Score presence of OCR/extraction artifacts."""
    issues = []
    artifact_count = 0
    margin_marker_count = 0

    full_text = " ".join(text for _, text in blocks)

    for pattern in ARTIFACT_PATTERNS:
        matches = pattern.findall(full_text)
        if matches:
            artifact_count += len(matches)
            issues.append(f"Artifact pattern '{pattern.pattern}': {len(matches)} matches")

    # Check for leaked margin markers (single letters A-H between sentences)
    margin_pattern = re.compile(r"(?<=[.!?])\s+[A-H]\s+(?=[A-Z])")
    margin_matches = margin_pattern.findall(full_text)
    margin_marker_count = len(margin_matches)
    if margin_marker_count:
        issues.append(f"Margin markers leaked: {margin_marker_count}")

    total_chars = len(full_text)
    if total_chars == 0:
        return 100.0, 0, 0, []

    # Score: penalize based on artifact density
    artifact_density = (artifact_count + margin_marker_count) / (total_chars / 1000)
    score = max(0, 100 - (artifact_density * 10))

    return score, artifact_count, margin_marker_count, issues


def _score_structure(blocks: list[tuple[str, str]]) -> tuple[float, list[str]]:
    """Score overall document structure."""
    issues = []

    if not blocks:
        return 0.0, ["Empty document"]

    # Check for reasonable paragraph lengths
    para_lengths = [len(text.split()) for tag, text in blocks if tag == "p"]

    if not para_lengths:
        return 50.0, ["No paragraphs found"]

    avg_length = sum(para_lengths) / len(para_lengths)

    score = 100.0

    # Very short average paragraph length indicates fragmentation
    if avg_length < 10:
        penalty = (10 - avg_length) * 5
        score -= penalty
        issues.append(f"Low average paragraph length: {avg_length:.1f} words")

    # Check heading-to-paragraph ratio
    heading_count = sum(1 for tag, _ in blocks if tag.startswith("h"))
    para_count = len(para_lengths)

    if para_count > 0 and heading_count / para_count > 0.3:
        score -= 20
        issues.append(f"High heading ratio: {heading_count}/{para_count}")

    return max(0, score), issues


def score_conversion(html_content: str, source_path: Path | None = None) -> FidelityReport:  # noqa: ARG001
    """
    Score the fidelity of a PDF-to-HTML conversion.

    Args:
        html_content: The converted HTML content
        source_path: Optional path to source PDF (for reference in report)

    Returns:
        FidelityReport with detailed metrics and issues
    """
    soup = BeautifulSoup(html_content, "html.parser")

    if not soup.body:
        return FidelityReport(
            text_continuity_score=0,
            heading_accuracy_score=0,
            artifact_score=0,
            structure_score=0,
            issues=["No body element found in HTML"],
        )

    blocks = _extract_text_blocks(soup)

    all_issues = []

    continuity_score, fragmented, continuity_issues = _score_text_continuity(blocks)
    all_issues.extend(continuity_issues)

    heading_score, false_headings, heading_issues = _score_heading_accuracy(blocks)
    all_issues.extend(heading_issues)

    artifact_score, artifacts, margin_markers, artifact_issues = _score_artifacts(blocks)
    all_issues.extend(artifact_issues)

    structure_score, structure_issues = _score_structure(blocks)
    all_issues.extend(structure_issues)

    return FidelityReport(
        text_continuity_score=continuity_score,
        heading_accuracy_score=heading_score,
        artifact_score=artifact_score,
        structure_score=structure_score,
        fragmented_lines=fragmented,
        false_headings=false_headings,
        artifacts_found=artifacts,
        margin_markers_leaked=margin_markers,
        issues=all_issues,
    )


def score_conversion_from_file(html_path: Path) -> FidelityReport:
    """Score conversion fidelity from an HTML file."""
    html_content = html_path.read_text(encoding="utf-8")
    return score_conversion(html_content, html_path)
