"""BaseStackHandler ABC — abstract base for all stack handlers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .helpers import iter_source_files


class BaseStackHandler(ABC):
    """Abstract base class for stack-specific scoring handlers."""

    # --- abstract properties (override in subclass) ---

    @property
    @abstractmethod
    def name(self) -> str:
        """Stack identifier, e.g. 'go', 'python', 'node'."""

    @property
    @abstractmethod
    def marker_files(self) -> list[str]:
        """Files whose presence indicates this stack, e.g. ['go.mod']."""

    @property
    @abstractmethod
    def comment_prefix(self) -> str:
        """Single-line comment prefix, e.g. '//' or '#'."""

    @property
    @abstractmethod
    def source_globs(self) -> list[str]:
        """Glob patterns for source files, e.g. ['*.go']."""

    @property
    def extra_src_dirs(self) -> list[str]:
        """Extra source directories beyond standard set. Override if needed."""
        return []

    @property
    @abstractmethod
    def correctness_proxies(self) -> list[dict[str, Any]]:
        """Grep-based correctness proxy patterns for this stack."""

    @property
    @abstractmethod
    def source_extensions(self) -> set[str]:
        """File extensions for correctness proxy scanning, e.g. {'.go'}."""

    # --- abstract scoring methods ---

    @abstractmethod
    def score_build(self, fixture_path: Path) -> dict[str, Any]:
        """Score build success (max 10 pts)."""

    @abstractmethod
    def score_unit_tests(self, fixture_path: Path) -> dict[str, Any]:
        """Score unit test pass rate (max 10 pts)."""

    @abstractmethod
    def score_linting(self, fixture_path: Path) -> dict[str, Any]:
        """Score linting results (max 6 pts)."""

    @abstractmethod
    def score_complexity(self, fixture_path: Path) -> dict[str, Any]:
        """Score cyclomatic complexity (max 4 pts)."""

    @abstractmethod
    def score_security(self, fixture_path: Path, kloc: float) -> dict[str, Any]:
        """Score security analysis (max 4 pts)."""

    # --- concrete methods (shared logic) ---

    def detect(self, fixture_path: Path) -> bool:
        """Check if this stack is present in the fixture."""
        return any((fixture_path / m).exists() for m in self.marker_files)

    def check_toolchain_available(self, fixture_path: Path) -> bool:
        """Check if the primary toolchain is available. Override per stack."""
        return True

    def check_correctness_proxies(self, fixture_path: Path) -> list[dict[str, Any]]:
        """Run grep-based correctness proxy checks (advisory only)."""
        proxies = self.correctness_proxies
        extensions = self.source_extensions
        if not proxies:
            return []

        flags: list[dict[str, Any]] = []
        for file in iter_source_files(
            fixture_path, stack=self.name, extra_src_dirs=self.extra_src_dirs
        ):
            if file.suffix not in extensions:
                continue
            try:
                content = file.read_text(errors="ignore")
            except Exception:
                continue

            lines = content.splitlines()
            for proxy in proxies:
                for line in lines:
                    if re.search(proxy["pattern"], line):
                        guard = proxy.get("guard")
                        if guard is not None and re.search(guard, line):
                            continue
                        flags.append(
                            {
                                "id": proxy["id"],
                                "file": str(file.relative_to(fixture_path)),
                                "description": proxy["description"],
                            }
                        )

        return flags
