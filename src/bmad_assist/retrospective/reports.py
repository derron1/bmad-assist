"""Retrospective report extraction and persistence module.

Bug Fix: Retrospective Report Persistence

This module provides:
- extract_retrospective_report(): Extract report from LLM output using markers
- save_retrospective_report(): Save retrospective report to file

Extraction Strategy (via shared core/extraction.py):
1. Primary: Extract content between <!-- RETROSPECTIVE_REPORT_START --> and
   <!-- RETROSPECTIVE_REPORT_END --> markers
2. Fallback: Look for "# Epic" header pattern and extract from there
3. Last resort: Return raw output stripped

"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from bmad_assist.core.extraction import RETROSPECTIVE_MARKERS, extract_report
from bmad_assist.core.io import atomic_write
from bmad_assist.core.types import EpicId

logger = logging.getLogger(__name__)

__all__ = [
    "extract_retrospective_report",
    "save_retrospective_report",
]


def extract_retrospective_report(raw_output: str) -> str:
    r"""Extract retrospective report content from LLM output.

    Uses shared extraction logic from core/extraction.py:
    1. Primary: Extract between <!-- RETROSPECTIVE_REPORT_START/END --> markers
    2. Fallback: Look for "# Epic N Retrospective" or "RETROSPECTIVE COMPLETE"
    3. Last resort: Return entire output stripped

    Args:
        raw_output: Raw LLM output (stdout from provider).

    Returns:
        Extracted report content. Never returns empty string.

    Example:
        >>> output = '''Amelia (Developer): "Starting retro..."
        ... <!-- RETROSPECTIVE_REPORT_START -->
        ... # Epic 21 Retrospective: Notification Format Enhancement
        ... ...report content...
        ... <!-- RETROSPECTIVE_REPORT_END -->
        ... Amelia: "Meeting adjourned!"'''
        >>> extract_retrospective_report(output)
        '# Epic 21 Retrospective: Notification Format Enhancement\\n...report content...'

    """
    return extract_report(raw_output, RETROSPECTIVE_MARKERS)


def save_retrospective_report(
    content: str,
    epic_id: EpicId,
    retrospectives_dir: Path,
    timestamp: datetime | None = None,
) -> Path:
    """Save a retrospective report to file.

    File path pattern:
    {retrospectives_dir}/epic-{epic_id}-retro-{YYYYMMDD}.md

    Args:
        content: Extracted retrospective report content.
        epic_id: Epic identifier (int or string like "testarch").
        retrospectives_dir: Path to retrospectives directory.
        timestamp: Optional timestamp for filename. If None, uses now().

    Returns:
        Path to saved report file.

    Raises:
        OSError: If write fails.

    """
    if timestamp is None:
        timestamp = datetime.now(UTC)

    # Format date for filename (YYYYMMDD)
    date_str = timestamp.strftime("%Y%m%d")

    # Build filename
    filename = f"epic-{epic_id}-retro-{date_str}.md"
    file_path = retrospectives_dir / filename

    # Check for existing file (overwrite with warning)
    if file_path.exists():
        logger.warning("Overwriting existing retrospective report: %s", file_path)

    atomic_write(file_path, content)

    logger.info("Saved retrospective report: %s", file_path)
    return file_path
