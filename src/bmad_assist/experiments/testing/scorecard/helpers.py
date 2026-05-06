"""Shared utilities: soft-skip, test results, SLOC counting, TODO/placeholder scanning."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .constants import (
    EXCLUDED_DIRS,
    PLACEHOLDER_PATTERNS,
    SOURCE_EXTENSIONS,
    TEST_FILE_PATTERNS,
    TODO_PATTERN,
)


def get_fixtures_dir() -> Path:
    """Get the fixtures directory (relative to CWD)."""
    return Path.cwd() / "experiments" / "fixtures"


def get_scorecards_dir() -> Path:
    """Get the scorecards output directory (relative to CWD)."""
    return Path.cwd() / "experiments" / "analysis" / "scorecards"


def soft_skip(max_score: float, tool: str, reason: str) -> dict[str, Any]:
    """Return a soft-skip result with 40% partial credit and warning."""
    return {
        "max": max_score,
        "score": round(max_score * 0.4, 1),
        "skipped": True,
        "partial": True,
        "tool": tool,
        "warning": reason,
    }


def score_test_results(
    passed: int, failed: int, skipped: int = 0, max_score: float = 10.0
) -> dict[str, Any]:
    """Build unit test result dict from pass/fail/skip counts.

    Returns soft-skip when total==0 (no tests collected).
    """
    total = passed + failed
    if total == 0:
        return soft_skip(max_score, "tests", "no tests collected (0 passed, 0 failed)")
    return {
        "max": max_score,
        "score": round((passed / total) * max_score, 1),
        "metric": f"{passed}/{total}",
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": [],
    }


def get_src_dirs(
    fixture_path: Path, stack: str | None = None, extra_src_dirs: list[str] | None = None
) -> list[Path]:
    """Return list of standard source directories that exist."""
    candidates = [
        fixture_path / "src",
        fixture_path / "internal",
        fixture_path / "cmd",
        fixture_path / "lib",
        fixture_path / "app",
    ]

    # Add stack-specific extra dirs
    if extra_src_dirs:
        for extra_dir in extra_src_dirs:
            candidates.append(fixture_path / extra_dir)

    dirs = [d for d in candidates if d.exists()]

    # For Node stack, include fixture root for root-level .ts/.js files
    if stack == "node" and fixture_path not in dirs:
        dirs.append(fixture_path)

    return dirs


def iter_source_files(
    fixture_path: Path, stack: str | None = None, extra_src_dirs: list[str] | None = None
) -> list[Path]:
    """Iterate over all source files in standard directories, excluding vendor dirs."""
    seen: set[Path] = set()
    files = []
    src_dirs = get_src_dirs(fixture_path, stack=stack, extra_src_dirs=extra_src_dirs)
    for src_dir in src_dirs:
        # For the fixture root dir, only scan top-level files (avoid re-scanning subdirs)
        if src_dir == fixture_path:
            iterator = src_dir.glob("*")
        else:
            iterator = src_dir.rglob("*")
        for file in iterator:
            if file.is_file() and file.suffix in SOURCE_EXTENSIONS:
                # Skip files inside excluded directories
                try:
                    rel = file.relative_to(fixture_path)
                except ValueError:
                    continue
                if any(part in EXCLUDED_DIRS for part in rel.parts):
                    continue
                if file not in seen:
                    seen.add(file)
                    files.append(file)
    return files


def count_todos(fixture_path: Path, **kwargs: Any) -> tuple[int, list[str]]:
    """Count TODO/FIXME/XXX/HACK markers in source files."""
    count = 0
    file_list: list[str] = []
    for file in iter_source_files(fixture_path, **kwargs):
        try:
            content = file.read_text(errors="ignore")
            matches = TODO_PATTERN.findall(content)
            if matches:
                count += len(matches)
                file_list.append(f"{file.relative_to(fixture_path)} ({len(matches)})")
        except Exception:
            pass
    return count, file_list


def count_placeholders(fixture_path: Path, **kwargs: Any) -> tuple[int, list[str]]:
    """Count placeholder patterns in source files."""
    count = 0
    found_patterns: list[str] = []
    for file in iter_source_files(fixture_path, **kwargs):
        try:
            content = file.read_text(errors="ignore")
            for pattern, name in PLACEHOLDER_PATTERNS:
                hits = re.findall(pattern, content)
                if hits:
                    count += len(hits)
                    if name not in found_patterns:
                        found_patterns.append(name)
        except Exception:
            pass
    return count, found_patterns


def is_test_file(file: Path) -> bool:
    """Check if a file is a test file based on naming conventions."""
    checker = TEST_FILE_PATTERNS.get(file.suffix)
    if checker:
        return bool(checker(file.name))
    return False


def count_source_lines(fixture_path: Path, exclude_tests: bool = True, **kwargs: Any) -> int:
    """Count non-blank source lines, optionally excluding test files.

    Used for KLOC normalization in security scoring.
    """
    total = 0
    for file in iter_source_files(fixture_path, **kwargs):
        if exclude_tests and is_test_file(file):
            continue
        try:
            content = file.read_text(errors="ignore")
            total += sum(1 for line in content.splitlines() if line.strip())
        except Exception:
            pass
    return total
