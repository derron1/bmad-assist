"""Shared git-diff helpers for inlined code-review skill compilers.

Phase 7.2 inlines ``bmad-code-review`` and ``bmad-code-review-synthesis``
into self-contained skill compilers. Both need the same git-diff
capture and ``--stat`` parsing primitives that previously lived as
module-private helpers on
:mod:`bmad_assist.compiler.workflows.code_review`.

We extract them here so the inlined compilers do NOT cross-import the
legacy ``compiler.workflows`` package — Brief 7.3 deletes that
directory wholesale, and any lingering import would break.

Not part of the public ``compiler.skills`` API; the underscore prefix
mirrors :mod:`_synthesis_base` and :mod:`_testarch_base`. Keep this
module dependency-free apart from stdlib + the existing
``bmad_assist.git`` / ``bmad_assist.compiler`` surfaces it already
relies on.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from bmad_assist.compiler.types import CompilerContext
from bmad_assist.git import get_validated_diff

logger = logging.getLogger(__name__)

# Timeout for git commands in seconds.
GIT_TIMEOUT = 30

# Pattern for parsing ``git diff --stat`` output.
# Matches: " src/file.py | 42 ++++----" or " src/file.py | 42 +"
# Non-greedy match to support filenames with spaces.
_STAT_PATTERN = re.compile(r"^\s*(.+?)\s*\|\s*(\d+)", re.MULTILINE)

# Pattern for binary files: " file.bin | Bin 1234 -> 5678 bytes".
_BINARY_PATTERN = re.compile(r"^\s*[^\s|]+\s*\|\s*Bin\s+", re.MULTILINE)

# Pattern for renamed files with changes: " old.py => new.py | 5".
# Also handles brace-style: " src/{old => new}/file.py | 10".
# Captures new path and change count.
_RENAME_WITH_CHANGES_PATTERN = re.compile(
    r"^\s*(?:\{[^}]+\}\s*=>\s*)?(.+?)\s*=>\s*(.+?)\s*\|\s*(\d+)", re.MULTILINE
)


def capture_git_diff(context: CompilerContext) -> str:
    """Capture git diff with intelligent filtering and validation.

    Uses ``get_validated_diff`` which:
    - Detects proper merge-base (handles merge commits correctly).
    - Filters out cache/metadata files that cause false positives.
    - Validates diff quality (warns if too much garbage).

    Returns the filtered diff (with markers) or an empty string when
    the project root isn't a git repo, when git isn't installed, or
    when any error occurs.
    """
    project_root = context.project_root

    try:
        # Check if this is the git repository ROOT (not just a subdirectory).
        check_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_root,
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )

        if check_result.returncode != 0:
            logger.debug("Not a git repository: %s", project_root)
            return ""

        # Verify we're at the git root, not a subdirectory of another repo.
        git_root = Path(check_result.stdout.strip()).resolve()
        if git_root != project_root.resolve():
            logger.debug(
                "Project %s is inside git repo %s but not at root",
                project_root,
                git_root,
            )
            return ""

        diff_content, validation = get_validated_diff(
            project_root,
            max_garbage_ratio=0.3,
            raise_on_invalid=False,  # Warn but don't block.
        )

        if validation.total_files > 0:
            logger.debug(
                "Diff validation: %d source files, %d garbage files (%.0f%% garbage)",
                validation.source_files,
                validation.garbage_files,
                validation.garbage_ratio * 100,
            )

        if not validation.is_valid:
            logger.warning(
                "Diff quality warning: %s - review may have false positives",
                "; ".join(validation.issues),
            )

        return diff_content

    except FileNotFoundError:
        logger.warning("git command not found")
        return ""
    except subprocess.TimeoutExpired:
        logger.error("git command timed out after %ds", GIT_TIMEOUT)
        return ""
    except OSError as e:
        logger.warning("git command failed: %s", e)
        return ""


def extract_modified_files_from_stat(
    stat_output: str,
    skip_docs: bool = True,
    skip_generated: bool = True,
) -> list[tuple[str, int]]:
    """Extract modified file paths and change counts from ``git diff --stat``.

    Parses output like::

        src/file.py | 42 ++++++----
        tests/test.py | 25 ++++
        old.py => new.py | 5 +++--

    Returns a list of ``(path, change_count)`` tuples sorted by changes
    desc, path asc. Filters out binary files, pure renames (zero
    changes), and (when enabled) docs/generated artifacts.
    """
    # Extract only the stat section (before patch content starts).
    # Stat ends with a summary line like:
    #   "3 files changed, 25 insertions(+), 10 deletions(-)"
    stat_end_pattern = re.compile(r"^\s*\d+\s+files?\s+changed", re.MULTILINE | re.IGNORECASE)
    stat_end_match = stat_end_pattern.search(stat_output)
    if stat_end_match:
        stat_output = stat_output[: stat_end_match.end()]

    # Find all binary files to skip.
    binary_matches = set()
    for match in _BINARY_PATTERN.finditer(stat_output):
        line = match.group(0)
        path_match = re.match(r"^\s*([^\s|]+)", line)
        if path_match:
            binary_matches.add(path_match.group(1))

    result: list[tuple[str, int]] = []
    seen_paths: set[str] = set()

    # First, process renamed files (they have => in the line).
    for match in _RENAME_WITH_CHANGES_PATTERN.finditer(stat_output):
        new_path = match.group(2).strip()
        try:
            changes = int(match.group(3))
        except ValueError:
            continue

        # Skip pure renames with zero content changes.
        if changes == 0:
            continue

        if skip_docs and new_path.startswith("docs/"):
            continue

        if skip_generated and (
            new_path.startswith("_bmad-output/")
            or new_path.startswith(".bmad-assist/")
            or "/_bmad-output/" in new_path
            or "/.bmad-assist/" in new_path
        ):
            continue

        if new_path not in seen_paths:
            result.append((new_path, changes))
            seen_paths.add(new_path)

    # Then, process regular files (no =>).
    for match in _STAT_PATTERN.finditer(stat_output):
        path = match.group(1)
        try:
            changes = int(match.group(2))
        except ValueError:
            continue

        if path in binary_matches:
            continue

        # Skip files that have => in them (already captured above).
        if "=>" in path:
            continue

        if skip_docs and path.startswith("docs/"):
            continue

        if skip_generated and (
            path.startswith("_bmad-output/")
            or path.startswith(".bmad-assist/")
            or "/_bmad-output/" in path
            or "/.bmad-assist/" in path
        ):
            continue

        if path not in seen_paths:
            result.append((path, changes))
            seen_paths.add(path)

    # Sort by changes desc, then path asc for determinism.
    result.sort(key=lambda x: (-x[1], x[0]))

    return result


__all__ = [
    "GIT_TIMEOUT",
    "capture_git_diff",
    "extract_modified_files_from_stat",
]
