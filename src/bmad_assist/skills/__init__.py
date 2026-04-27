"""Bundled BMAD v6.4+ skill sources for bmad-assist.

Phase 2 of the skill-layout refactor introduces this package as the
sibling of :mod:`bmad_assist.workflows`. It holds bundled ``SKILL.md``
sources plus their cached compiled templates so the new compiler path
can resolve a skill even when the user hasn't installed BMAD locally.

Public helpers:
    get_bundled_skill_dir: Return path to a bundled ``<skill-id>/`` dir.
    get_bundled_skill_md: Return path to a bundled ``SKILL.md`` file.
    list_bundled_skills: List the bundled skill IDs.
"""

from __future__ import annotations

import logging
import sys
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)

# Python 3.14+ moved Traversable to importlib.resources.abc
if sys.version_info >= (3, 14):
    from importlib.resources.abc import Traversable
else:
    from importlib.abc import Traversable


def get_bundled_skill_dir(skill_id: str) -> Path | None:
    """Return absolute path to a bundled skill directory, or ``None``.

    Args:
        skill_id: Skill identifier (e.g. ``"bmad-create-story"``).

    Returns:
        Filesystem path to the bundled ``<skill_id>/`` directory if it
        ships with bmad-assist and contains a ``SKILL.md``. ``None``
        otherwise (skill not bundled, or running from a zip/wheel that
        doesn't expose the file as a real path).

    """
    try:
        package_path: Traversable = files("bmad_assist.skills")
        skill_path: Traversable = package_path / skill_id

        if not skill_path.is_dir():
            return None

        skill_md: Traversable = skill_path / "SKILL.md"
        if not skill_md.is_file():
            return None

        return Path(str(skill_path))
    except Exception:
        logger.debug("Failed to resolve bundled skill dir for %s", skill_id, exc_info=True)
        return None


def get_bundled_skill_md(skill_id: str) -> Path | None:
    """Return absolute path to a bundled ``SKILL.md`` for ``skill_id``."""
    skill_dir = get_bundled_skill_dir(skill_id)
    if skill_dir is None:
        return None
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    return skill_md


def list_bundled_skills() -> list[str]:
    """List bundled skill IDs (directories that contain ``SKILL.md``)."""
    try:
        package_path: Traversable = files("bmad_assist.skills")
        skills: list[str] = []
        for item in package_path.iterdir():
            if item.is_dir() and (item / "SKILL.md").is_file():
                skills.append(item.name)
        return sorted(skills)
    except Exception:
        logger.debug("Failed to list bundled skills", exc_info=True)
        return []


__all__ = [
    "get_bundled_skill_dir",
    "get_bundled_skill_md",
    "list_bundled_skills",
]
