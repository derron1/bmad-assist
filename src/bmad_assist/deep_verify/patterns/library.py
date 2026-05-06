"""Pattern library for Deep Verify.

This module provides the PatternLibrary class for loading, storing,
and retrieving verification patterns from YAML files.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from bmad_assist.core.exceptions import PatternLibraryError, PatternNotFoundError
from bmad_assist.deep_verify.core.types import (
    ArtifactDomain,
    Pattern,
    PatternId,
    Severity,
    Signal,
)

logger = logging.getLogger(__name__)

# Pattern ID format: XX-NNN or XXX-NNN (e.g., "CC-001", "SEC-004", "DB-005")
# Extended for code patterns: XX-NNN-CODE or XX-NNN-CODE-LANG (e.g., "CC-001-CODE", "CC-001-CODE-GO")
PATTERN_ID_REGEX = re.compile(r"^[A-Z]{2,3}-\d{3}(-CODE(-[A-Z]{2,})?)?$")

# Language code mapping - shared across methods for consistency
LANGUAGE_CODE_MAP: dict[str, str] = {
    "go": "go",
    "golang": "go",
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "rs": "rust",
    "rust": "rust",
    "java": "java",
    "rb": "ruby",
    "ruby": "ruby",
}


def _parse_yaml_signal(yaml_signal: str) -> Signal:
    """Parse a signal string from YAML into a Signal object.

    Args:
        yaml_signal: Signal string from YAML, optionally prefixed with "regex:".

    Returns:
        Signal object with appropriate type.

    """
    if yaml_signal.startswith("regex:"):
        return Signal(
            type="regex",
            pattern=yaml_signal[6:],  # Strip "regex:" prefix
            weight=1.0,
        )
    return Signal(
        type="exact",
        pattern=yaml_signal,
        weight=1.0,
    )


class PatternLibrary:
    """Library of verification patterns loaded from YAML files.

    The library supports loading patterns from multiple directories,
    with later files overriding earlier ones for the same pattern ID.

    Attributes:
        _patterns: Dictionary mapping pattern IDs to Pattern objects.
        _compiled_regexes: Dictionary mapping (pattern_id, signal_idx) to compiled regex.

    """

    def __init__(self) -> None:
        """Initialize an empty pattern library."""
        self._patterns: dict[PatternId, Pattern] = {}
        self._compiled_regexes: dict[tuple[PatternId, int], re.Pattern[str]] = {}
        self._pattern_sources: dict[PatternId, Path] = {}

    def __len__(self) -> int:
        """Return the number of patterns in the library."""
        return len(self._patterns)

    def __repr__(self) -> str:
        """Return a string representation of the library."""
        return f"PatternLibrary(patterns={len(self._patterns)})"

    @classmethod
    def load(cls, paths: list[Path]) -> PatternLibrary:
        """Load patterns from YAML files in the specified paths.

        Files within each directory are loaded recursively and alphabetically.
        Later paths override earlier paths for the same pattern ID.

        Args:
            paths: List of paths to directories or YAML files.

        Returns:
            PatternLibrary with loaded patterns.

        Raises:
            PatternLibraryError: If a YAML file has invalid structure or content.

        """
        library = cls()

        for path in paths:
            if path.is_dir():
                # Load all YAML files recursively, sorted alphabetically by full path
                # This ensures deterministic loading order across subdirectories
                yaml_files = sorted(
                    list(path.rglob("*.yaml")) + list(path.rglob("*.yml")),
                    key=lambda p: str(p),
                )
                for yaml_file in yaml_files:
                    library._load_yaml_file(yaml_file)
            elif path.is_file() and path.suffix in (".yaml", ".yml"):
                library._load_yaml_file(path)
            else:
                logger.warning("Skipping non-YAML path: %s", path)

        return library

    def _load_yaml_file(self, path: Path) -> None:
        """Load patterns from a single YAML file.

        Args:
            path: Path to the YAML file.

        Raises:
            PatternLibraryError: If the YAML file has invalid structure or content.

        """
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise PatternLibraryError(
                f"Invalid YAML syntax: {e}",
                file_path=path,
            ) from e
        except OSError as e:
            raise PatternLibraryError(
                f"Cannot read file: {e}",
                file_path=path,
            ) from e

        if data is None:
            logger.debug("Empty YAML file: %s", path)
            return

        if not isinstance(data, dict):
            raise PatternLibraryError(
                f"YAML root must be a dictionary, got {type(data).__name__}",
                file_path=path,
            )

        patterns_data = data.get("patterns")
        if patterns_data is None:
            logger.debug("No 'patterns' key in YAML file: %s", path)
            return

        if not isinstance(patterns_data, list):
            raise PatternLibraryError(
                f"'patterns' must be a list, got {type(patterns_data).__name__}",
                file_path=path,
            )

        for idx, pattern_data in enumerate(patterns_data):
            try:
                pattern = self._parse_pattern(pattern_data, path)

                # Check for duplicate and log warning with both file paths
                if pattern.id in self._patterns:
                    original_path = self._pattern_sources.get(pattern.id, "unknown")
                    logger.warning(
                        "Duplicate pattern ID '%s': %s overrides previous definition from %s",
                        pattern.id,
                        path,
                        original_path,
                    )

                self._patterns[pattern.id] = pattern
                self._pattern_sources[pattern.id] = path

                # Pre-compile regex signals
                self._compile_regexes(pattern)

            except PatternLibraryError as e:
                # Add pattern index to context
                raise PatternLibraryError(
                    f"Error in pattern at index {idx}: {e}",
                    file_path=path,
                ) from e

    def _parse_pattern(self, data: dict[str, Any], file_path: Path) -> Pattern:
        """Parse a pattern from YAML data.

        Args:
            data: Dictionary containing pattern data.
            file_path: Path to the source file (for error context).

        Returns:
            Parsed Pattern object.

        Raises:
            PatternLibraryError: If the pattern data is invalid.

        """
        # Validate required fields
        if not isinstance(data, dict):
            raise PatternLibraryError(
                f"Pattern must be a dictionary, got {type(data).__name__}",
                file_path=file_path,
            )

        pattern_id = data.get("id")
        if not pattern_id:
            raise PatternLibraryError(
                "Pattern missing required 'id' field",
                file_path=file_path,
            )

        # Validate pattern ID format: XX-NNN or XX-NNN-CODE or XX-NNN-CODE-LANG
        if not PATTERN_ID_REGEX.match(pattern_id):
            raise PatternLibraryError(
                f"Invalid pattern ID format '{pattern_id}': expected XX-NNN or XX-NNN-CODE[-LANG] (e.g., CC-001, CC-001-CODE, CC-001-CODE-GO)",
                file_path=file_path,
                pattern_id=pattern_id,
            )

        # Validate domain
        domain_str = data.get("domain")
        if not domain_str:
            raise PatternLibraryError(
                f"Pattern '{pattern_id}' missing required 'domain' field",
                file_path=file_path,
                pattern_id=pattern_id,
            )

        try:
            domain = ArtifactDomain(domain_str.lower())
        except ValueError as e:
            valid_domains = [d.value for d in ArtifactDomain]
            raise PatternLibraryError(
                f"Invalid domain '{domain_str}' for pattern '{pattern_id}'. "
                f"Valid domains: {valid_domains}",
                file_path=file_path,
                pattern_id=pattern_id,
            ) from e

        # Validate severity
        severity_str = data.get("severity")
        if not severity_str:
            raise PatternLibraryError(
                f"Pattern '{pattern_id}' missing required 'severity' field",
                file_path=file_path,
                pattern_id=pattern_id,
            )

        try:
            severity = Severity(severity_str.lower())
        except ValueError as e:
            valid_severities = [s.value for s in Severity]
            raise PatternLibraryError(
                f"Invalid severity '{severity_str}' for pattern '{pattern_id}'. "
                f"Valid severities: {valid_severities}",
                file_path=file_path,
                pattern_id=pattern_id,
            ) from e

        # Parse signals
        signals_data = data.get("signals", [])
        if not isinstance(signals_data, list):
            raise PatternLibraryError(
                f"Pattern '{pattern_id}' signals must be a list",
                file_path=file_path,
                pattern_id=pattern_id,
            )

        signals = [_parse_yaml_signal(str(s)) for s in signals_data]

        # Optional fields
        description = data.get("description")
        remediation = data.get("remediation")

        # Extract language from file path for code patterns
        # e.g., patterns/data/code/go/concurrency.yaml -> "go"
        language = self._extract_language_from_path(file_path)

        return Pattern(
            id=PatternId(pattern_id),
            domain=domain,
            signals=signals,
            severity=severity,
            description=description,
            remediation=remediation,
            language=language,
        )

    def _extract_language_from_path(self, file_path: Path) -> str | None:
        """Extract language code from file path for code patterns.

        Args:
            file_path: Path to the pattern file.

        Returns:
            Language code (e.g., "go", "python") if in code/ subdirectory,
            None otherwise (for spec patterns).

        """
        # Normalize path separators and convert to parts
        try:
            parts = file_path.parts
            # Look for "code" directory in path - use last occurrence to handle
            # paths like /home/user/code/project/.../patterns/data/code/go/
            if "code" in parts:
                # Find last occurrence to avoid matching "code" in parent paths
                code_idx = len(parts) - 1 - parts[::-1].index("code")
                # Language is the subdirectory after "code"
                if code_idx + 1 < len(parts):
                    lang_dir = parts[code_idx + 1]
                    return LANGUAGE_CODE_MAP.get(lang_dir.lower(), lang_dir.lower())
        except (ValueError, AttributeError):
            pass
        return None

    def _compile_regexes(self, pattern: Pattern) -> None:
        """Pre-compile regex signals for a pattern.

        Args:
            pattern: The pattern to compile regexes for.

        Raises:
            PatternLibraryError: If a regex signal has invalid syntax.

        """
        for idx, signal in enumerate(pattern.signals):
            if signal.type == "regex":
                try:
                    # Use IGNORECASE for case-insensitive matching
                    # Use DOTALL so . matches newlines (important for multiline code)
                    compiled = re.compile(signal.pattern, re.IGNORECASE | re.DOTALL)
                    self._compiled_regexes[(pattern.id, idx)] = compiled
                except re.error as e:
                    raise PatternLibraryError(
                        f"Invalid regex pattern in signal '{signal.pattern}': {e}",
                        pattern_id=pattern.id,
                    ) from e

    def get_pattern(
        self, pattern_id: PatternId | str, *, raise_on_missing: bool = False
    ) -> Pattern | None:
        """Get a single pattern by ID.

        Args:
            pattern_id: The pattern ID to look up.
            raise_on_missing: If True, raise PatternNotFoundError instead of returning None.

        Returns:
            The Pattern if found, None otherwise (unless raise_on_missing=True).

        Raises:
            PatternNotFoundError: If pattern not found and raise_on_missing=True.

        """
        pattern_id_str = pattern_id if isinstance(pattern_id, str) else pattern_id

        pattern = self._patterns.get(PatternId(pattern_id_str))
        if pattern is None and raise_on_missing:
            raise PatternNotFoundError(
                f"Pattern '{pattern_id_str}' not found in library",
                pattern_id=pattern_id_str,
            )
        return pattern

    def get_patterns(
        self,
        domains: list[ArtifactDomain] | None = None,
        language: str | None = None,
    ) -> list[Pattern]:
        """Get patterns filtered by domain and/or language.

        Args:
            domains: Optional list of domains to filter by. If None, no
                domain filter is applied.
            language: Optional language code (e.g., "go", "python") to scope
                code patterns. When specified, returns spec patterns
                (``pattern.language is None``) plus code patterns for that
                language. When ``None`` (no language hint), returns
                language-agnostic spec patterns ONLY — language-tagged code
                patterns are excluded so they don't fire across files of the
                wrong language. To retrieve every pattern in the library
                regardless of language, use :meth:`get_all_patterns`.

        Returns:
            List of patterns matching the filter criteria.

        """
        patterns = list(self._patterns.values())

        if domains:
            domain_set = set(domains)
            patterns = [p for p in patterns if p.domain in domain_set]

        if language:
            # Normalize language to lowercase for case-insensitive matching
            lang_lower = language.lower()
            # Map common aliases to canonical forms
            canonical_lang = LANGUAGE_CODE_MAP.get(lang_lower, lang_lower)

            # Filter to: spec patterns (language=None) + code patterns for requested language
            patterns = [p for p in patterns if p.language is None or p.language == canonical_lang]

            # Log warning if no code patterns found for this language
            code_patterns = [p for p in patterns if p.language == canonical_lang]
            if not code_patterns:
                logger.warning(
                    "No code patterns found for language '%s', returning spec patterns only",
                    language,
                )
        else:
            # No language hint: return spec-only patterns. This prevents
            # cross-language false positives (e.g. a Go-tagged "defer inside
            # loop" pattern matching a Python `for` loop) when callers cannot
            # supply a language. Callers wanting every pattern should use
            # get_all_patterns().
            patterns = [p for p in patterns if p.language is None]

        # Sort by pattern ID for deterministic ordering
        return sorted(patterns, key=lambda p: p.id)

    def get_all_patterns(self) -> list[Pattern]:
        """Get every pattern in the library, including language-tagged ones.

        Unlike :meth:`get_patterns` with ``language=None`` (which restricts
        to language-agnostic spec patterns), this returns the full set —
        spec + every language-specific code pattern. Use this only when you
        intentionally want to inspect or process the full library; for
        analysis paths driven by a particular file, prefer
        ``get_patterns(language=...)``.

        Returns:
            List of all patterns, sorted by ID.

        """
        return sorted(self._patterns.values(), key=lambda p: p.id)

    def get_compiled_regex(self, pattern_id: PatternId, signal_idx: int) -> re.Pattern[str] | None:
        """Get a pre-compiled regex for a signal.

        Args:
            pattern_id: The pattern ID.
            signal_idx: The index of the signal in the pattern.

        Returns:
            The compiled regex pattern if available, None otherwise.

        """
        return self._compiled_regexes.get((pattern_id, signal_idx))


@lru_cache(maxsize=1)
def get_default_pattern_library() -> PatternLibrary:
    """Load and cache the default pattern library.

    Loads both spec patterns (language-agnostic) and code patterns (language-specific).
    Spec patterns load first, then code patterns extend the library.
    Code patterns use -CODE suffix to avoid ID collisions with spec patterns.

    Uses lru_cache for singleton behavior across calls.

    Example:
        >>> from bmad_assist.deep_verify.patterns.library import get_default_pattern_library
        >>> library = get_default_pattern_library()
        >>> len(library) > 0
        True

    To get patterns for a specific language:
        >>> patterns = library.get_patterns(language="go")

    """
    data_dir = Path(__file__).parent / "data"
    spec_dir = data_dir / "spec"
    code_dir = data_dir / "code"

    # Load spec patterns first, then code patterns (later override earlier)
    paths: list[Path] = [spec_dir]
    if code_dir.exists():
        paths.append(code_dir)

    return PatternLibrary.load(paths)
