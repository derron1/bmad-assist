"""Exceptions raised by the ``skill_layout`` package.

All errors derive from :class:`SkillLayoutError`, so callers can catch
the package's surface with a single ``except`` clause when they prefer
not to discriminate.
"""

from __future__ import annotations


class SkillLayoutError(Exception):
    """Base exception for every error raised by the skill_layout package."""


class SkillNotFound(SkillLayoutError):  # noqa: N818 — name fixed by Phase 1 spec
    """Raised when a requested skill cannot be located on disk."""


class MalformedSkill(SkillLayoutError):  # noqa: N818 — name fixed by Phase 1 spec
    """Raised when a ``SKILL.md`` file cannot be parsed."""


class ManifestError(SkillLayoutError):
    """Raised when ``_bmad/_config/skill-manifest.csv`` cannot be read or is malformed."""


class ResolverError(SkillLayoutError):
    """Raised when the customization/config resolver cannot complete the merge."""
