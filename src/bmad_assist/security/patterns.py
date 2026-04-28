"""CWE pattern loader with dynamic token budget.

Loads tiered security patterns from bundled YAML files,
applying token budget constraints to fit within model context window.
"""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Approximate tokens per pattern entry (includes code examples)
TOKENS_PER_ENTRY = 90

# Tier definitions (lower number = higher priority)
TIER_HIGH = 1
TIER_MEDIUM = 2
TIER_LOW = 3


def get_pattern_dir() -> Path:
    """Resolve bundled security-review pattern directory.

    Returns:
        Path to the ``patterns/`` directory inside the bundled
        ``bmad-security-review`` skill.

    Raises:
        FileNotFoundError: If the bundled pattern directory cannot be
            located (typically a broken install).

    """
    # Phase 6: patterns ship under the bmad-security-review skill bundle
    # (the legacy workflows/security-review/ tree was removed when the
    # v6.4+ skill layout became the only routing path).
    try:
        pkg = importlib.resources.files("bmad_assist.skills")
        patterns_dir = Path(str(pkg)) / "bmad-security-review" / "patterns"
        if patterns_dir.is_dir():
            return patterns_dir
    except (ModuleNotFoundError, TypeError):
        pass

    # Fallback: resolve relative to this file (development checkouts where
    # importlib.resources cannot resolve the package directory).
    fallback = Path(__file__).parent.parent / "skills" / "bmad-security-review" / "patterns"
    if fallback.is_dir():
        return fallback

    raise FileNotFoundError(
        "Security review pattern directory not found. Reinstall: pip install -e ."
    )


def load_security_patterns(
    languages: list[str],
    available_tokens: int = 8000,
) -> list[dict[str, Any]]:
    """Load CWE security patterns with dynamic token budget.

    Always loads core.yaml, then per-language files. Applies tiered
    filtering to fit within token budget.

    Args:
        languages: Detected language identifiers (e.g., ["go", "python"]).
        available_tokens: Token budget for patterns (default 8000).

    Returns:
        List of pattern dicts, prioritized by tier and severity.

    """
    try:
        pattern_dir = get_pattern_dir()
    except FileNotFoundError:
        logger.warning("Pattern directory not found, returning empty patterns")
        return []

    all_patterns: list[dict[str, Any]] = []

    # Always load core patterns
    core_file = pattern_dir / "core.yaml"
    core_patterns = _load_pattern_file(core_file)
    all_patterns.extend(core_patterns)

    # Load language-specific patterns
    for lang in languages:
        lang_file = pattern_dir / f"{lang}.yaml"
        lang_patterns = _load_pattern_file(lang_file)
        all_patterns.extend(lang_patterns)

    if not all_patterns:
        logger.warning("No security patterns loaded")
        return []

    # Apply tiered filtering with token budget
    filtered = _apply_token_budget(all_patterns, available_tokens)

    logger.info(
        "Loaded %d security patterns (%d before budget filter, languages: %s)",
        len(filtered),
        len(all_patterns),
        languages or ["core-only"],
    )

    return filtered


def _load_pattern_file(file_path: Path) -> list[dict[str, Any]]:
    """Load patterns from a single YAML file.

    Args:
        file_path: Path to YAML pattern file.

    Returns:
        List of pattern dicts. Empty list on error.

    """
    if not file_path.exists():
        logger.debug("Pattern file not found: %s", file_path.name)
        return []

    try:
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.warning("Malformed YAML in %s, skipping: %s", file_path.name, e)
        return []
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path.name, e)
        return []

    if not isinstance(data, dict):
        logger.warning("Invalid format in %s: expected dict, got %s", file_path.name, type(data))
        return []

    patterns = data.get("patterns", [])
    if not isinstance(patterns, list):
        logger.warning("Invalid 'patterns' in %s: expected list", file_path.name)
        return []

    return patterns


def _apply_token_budget(
    patterns: list[dict[str, Any]],
    available_tokens: int,
) -> list[dict[str, Any]]:
    """Apply tiered token budget filtering.

    Priority: Tier 1 (HIGH) always → Tier 2 (MEDIUM) if budget → Tier 3 (LOW) optional.

    Args:
        patterns: All loaded patterns.
        available_tokens: Token budget.

    Returns:
        Filtered patterns within budget.

    """
    # Group by tier
    tier1 = [p for p in patterns if p.get("tier", TIER_MEDIUM) == TIER_HIGH]
    tier2 = [p for p in patterns if p.get("tier", TIER_MEDIUM) == TIER_MEDIUM]
    tier3 = [p for p in patterns if p.get("tier", TIER_MEDIUM) == TIER_LOW]

    result: list[dict[str, Any]] = []
    remaining_tokens = available_tokens

    # Tier 1 always included (warn if exceeds budget)
    for p in tier1:
        result.append(p)
        remaining_tokens -= TOKENS_PER_ENTRY
    if remaining_tokens < 0:
        logger.warning(
            "Tier 1 patterns alone exceed token budget (%d patterns, %d tokens over)",
            len(tier1),
            -remaining_tokens,
        )

    # Tier 2 if budget allows
    for p in tier2:
        if remaining_tokens < TOKENS_PER_ENTRY:
            logger.debug("Token budget exhausted at Tier 2, dropping remaining")
            break
        result.append(p)
        remaining_tokens -= TOKENS_PER_ENTRY

    # Tier 3 only if still budget
    for p in tier3:
        if remaining_tokens < TOKENS_PER_ENTRY:
            logger.debug("Token budget exhausted at Tier 3, dropping remaining")
            break
        result.append(p)
        remaining_tokens -= TOKENS_PER_ENTRY

    return result
