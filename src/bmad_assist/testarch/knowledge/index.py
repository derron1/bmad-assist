"""CSV index parsing for TEA Knowledge Base.

This module provides parsing of tea-index.csv into KnowledgeFragment objects
with validation and security checks.

Schema tolerance (Phase 4):
    The parser accepts both the legacy 5-column schema
    (``id,name,description,tags,fragment_file``) and the v6.4+ 6-column
    schema (``id,name,description,tags,tier,fragment_file``). For 5-col
    rows, ``tier`` defaults to ``"core"`` so downstream code can rely on
    a uniform shape. Any other column count fails loudly with a clear
    error message.

Usage:
    from bmad_assist.testarch.knowledge.index import parse_index

    fragments = parse_index(Path("_bmad/tea/testarch/tea-index.csv"))
"""

import csv
import logging
from pathlib import Path

from bmad_assist.core.exceptions import ParserError
from bmad_assist.testarch.knowledge.models import KnowledgeFragment

logger = logging.getLogger(__name__)

# Required CSV columns (5-column legacy schema). The 6-column v6.4+
# schema simply adds ``tier`` between ``tags`` and ``fragment_file``.
REQUIRED_COLUMNS = frozenset({"id", "name", "description", "tags", "fragment_file"})

# Optional column added in v6.4+ schemas.
OPTIONAL_COLUMNS = frozenset({"tier"})

# Default tier value applied to rows from a legacy 5-col index.
_DEFAULT_TIER = "core"


def _validate_path_security(fragment_file: str, row_num: int) -> bool:
    """Validate fragment file path for security.

    Args:
        fragment_file: Relative path to fragment file.
        row_num: Row number for error messages.

    Returns:
        True if path is safe, False otherwise.

    """
    # Reject absolute paths
    if Path(fragment_file).is_absolute():
        logger.warning(
            "Row %d: Absolute fragment path rejected: %s (security)",
            row_num,
            fragment_file,
        )
        return False

    # Reject path traversal
    if ".." in fragment_file:
        logger.warning(
            "Row %d: Fragment path with traversal rejected: %s (security)",
            row_num,
            fragment_file,
        )
        return False

    return True


def _parse_tags(tags_str: str) -> tuple[str, ...]:
    """Parse comma-separated tags string into tuple.

    Args:
        tags_str: Comma-separated tags (may be empty).

    Returns:
        Tuple of stripped, non-empty tags.

    """
    if not tags_str or not tags_str.strip():
        return ()

    return tuple(tag.strip() for tag in tags_str.split(",") if tag.strip())


def _validate_schema(fieldnames: list[str], index_path: Path) -> bool:
    """Validate the header row and report whether the schema includes ``tier``.

    Args:
        fieldnames: Header column names from the CSV.
        index_path: Path to the index file (for error messages).

    Returns:
        ``True`` if the schema includes the ``tier`` column (v6.4+),
        ``False`` for the legacy 5-column schema.

    Raises:
        ParserError: If required columns are missing, or if the column
            count is anything other than 5 (legacy) or 6 (v6.4+).

    """
    field_set = set(fieldnames)
    missing = REQUIRED_COLUMNS - field_set
    if missing:
        raise ParserError(
            f"Knowledge index missing required columns: {sorted(missing)}. "
            f"Required: {sorted(REQUIRED_COLUMNS)} (path: {index_path})"
        )

    has_tier = "tier" in field_set
    expected_count = 6 if has_tier else 5
    if len(fieldnames) != expected_count:
        # Either too few or too many columns. Build a helpful error.
        unexpected = field_set - REQUIRED_COLUMNS - OPTIONAL_COLUMNS
        raise ParserError(
            f"Knowledge index has unexpected schema: {len(fieldnames)} columns "
            f"({sorted(fieldnames)}). Expected 5 columns "
            f"({sorted(REQUIRED_COLUMNS)}) or 6 columns including 'tier'. "
            f"Unexpected columns: {sorted(unexpected)} (path: {index_path})"
        )

    return has_tier


def parse_index(index_path: Path) -> list[KnowledgeFragment]:
    """Parse tea-index.csv into list of KnowledgeFragment objects.

    Tolerates both the legacy 5-column schema and the v6.4+ 6-column
    schema (which adds ``tier``). For rows from a legacy index, ``tier``
    defaults to ``"core"``.

    Args:
        index_path: Path to tea-index.csv file.

    Returns:
        List of KnowledgeFragment objects in CSV order.
        Returns empty list if file is missing or empty (with warning).

    Raises:
        ParserError: If CSV is malformed (missing required columns or
            unexpected column count).

    """
    # Handle missing file gracefully (AC2)
    if not index_path.exists():
        logger.warning("Knowledge index not found: %s (returning empty list)", index_path)
        return []

    try:
        with open(index_path, encoding="utf-8", newline="") as f:
            content = f.read()
    except OSError as e:
        logger.warning("Failed to read knowledge index: %s (returning empty list)", e)
        return []

    # Handle empty file
    if not content.strip():
        logger.warning("Knowledge index is empty: %s (returning empty list)", index_path)
        return []

    # Parse CSV
    try:
        reader = csv.DictReader(content.splitlines())

        # Validate required columns exist (AC2)
        if reader.fieldnames is None:
            raise ParserError(f"Knowledge index has no header row: {index_path}")

        has_tier = _validate_schema(list(reader.fieldnames), index_path)

        fragments: list[KnowledgeFragment] = []
        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            # Get required fields
            fragment_id = row.get("id", "").strip()
            name = row.get("name", "").strip()
            description = row.get("description", "").strip()
            tags_str = row.get("tags", "").strip()
            fragment_file = row.get("fragment_file", "").strip()
            # Tier is optional — default to "core" for legacy 5-col rows.
            tier = (row.get("tier") or "").strip() if has_tier else _DEFAULT_TIER
            if has_tier and not tier:
                tier = _DEFAULT_TIER

            # Skip rows with missing required fields
            if not fragment_id:
                logger.warning("Row %d: Missing required field 'id', skipping", row_num)
                continue

            if not fragment_file:
                logger.warning(
                    "Row %d: Missing required field 'fragment_file', skipping",
                    row_num,
                )
                continue

            # Security validation
            if not _validate_path_security(fragment_file, row_num):
                continue

            # Parse tags
            tags = _parse_tags(tags_str)

            # Create fragment
            try:
                fragment = KnowledgeFragment(
                    id=fragment_id,
                    name=name,
                    description=description,
                    tags=tags,
                    fragment_file=fragment_file,
                    tier=tier,
                )
                fragments.append(fragment)
            except ValueError as e:
                logger.warning("Row %d: Invalid fragment data: %s, skipping", row_num, e)
                continue

        logger.debug("Parsed %d fragments from %s", len(fragments), index_path)
        return fragments

    except csv.Error as e:
        raise ParserError(f"Failed to parse knowledge index CSV: {e}") from e
