"""Locate a skill's ``SKILL.md`` on disk.

Probes are deterministic and ordered:

1. ``<project>/.claude/skills/<id>/SKILL.md`` — Claude Code mirror.
2. ``<project>/.agents/skills/<id>/SKILL.md`` — generic agent mirror.
3. The manifest's canonical path (when a manifest is supplied).

If both the Claude and agents mirrors carry the file but differ in
content, we log a warning. Phase 0 confirmed they are byte-identical
in normal installs — the warning is purely defensive.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from .errors import SkillNotFound
from .types import ManifestSkill

logger = logging.getLogger(__name__)

_CLAUDE_PREFIX = ".claude/skills"
_AGENTS_PREFIX = ".agents/skills"


def find_skill(
    skill_id: str,
    project_root: Path,
    manifest: dict[str, ManifestSkill] | None = None,
    bundled_fallback: bool = False,
) -> Path:
    """Return the absolute path to the skill's ``SKILL.md``.

    Args:
        skill_id: Skill identifier (e.g. ``"bmad-create-story"``,
            keeping the ``bmad-`` prefix).
        project_root: Project root used as the base for all probe paths.
        manifest: Optional manifest map (from
            :func:`bmad_assist.skill_layout.read_skill_manifest`). When
            supplied, its canonical path is used as the final fallback.
        bundled_fallback: When ``True``, the probe additionally checks
            the bmad-assist package's bundled ``skills/<skill_id>/``
            directory (under :mod:`bmad_assist.skills`) as the last
            resort before raising. Defaults to ``False`` for backwards
            compatibility with Phase 1 callers.

    Returns:
        Absolute path to the resolved ``SKILL.md`` file.

    Raises:
        SkillNotFound: If none of the probed paths contain the file.
            The exception message lists every path that was tried.

    """
    project_root = project_root.resolve()
    claude_candidate = project_root / _CLAUDE_PREFIX / skill_id / "SKILL.md"
    agents_candidate = project_root / _AGENTS_PREFIX / skill_id / "SKILL.md"

    claude_exists = claude_candidate.is_file()
    agents_exists = agents_candidate.is_file()

    if claude_exists and agents_exists:
        _warn_on_checksum_mismatch(skill_id, claude_candidate, agents_candidate)

    if claude_exists:
        return claude_candidate.resolve()
    if agents_exists:
        return agents_candidate.resolve()

    probed: list[Path] = [claude_candidate, agents_candidate]

    if manifest is not None and skill_id in manifest:
        canonical_raw = manifest[skill_id].canonical_path
        if canonical_raw:
            canonical = (project_root / canonical_raw).resolve()
            probed.append(canonical)
            if canonical.is_file():
                return canonical
            # Manifest may point at the directory (callers can be
            # inconsistent about trailing /SKILL.md). Try the directory
            # form too before giving up.
            if canonical.is_dir():
                fallback = canonical / "SKILL.md"
                probed.append(fallback)
                if fallback.is_file():
                    return fallback.resolve()

    if bundled_fallback:
        bundled = find_bundled_skill(skill_id)
        if bundled is not None:
            return bundled
        # Track the bundled path we tried so the error message lists it.
        try:
            from bmad_assist import skills as _skills_pkg

            probed.append(Path(_skills_pkg.__file__).parent / skill_id / "SKILL.md")
        except Exception:  # pragma: no cover — defensive only
            pass

    formatted = "\n  - ".join(str(path) for path in probed)
    raise SkillNotFound(
        f"skill '{skill_id}' not found under {project_root}; tried:\n  - {formatted}"
    )


def find_bundled_skill(skill_id: str) -> Path | None:
    """Return the path to a bundled ``SKILL.md`` for ``skill_id``, or ``None``.

    Looks the skill up under :mod:`bmad_assist.skills` (the bundled
    sources shipped with bmad-assist). Returns ``None`` when the skill
    is not bundled, when the package's resources cannot be exposed as
    real filesystem paths (e.g. zip-installed wheels), or when the
    expected ``SKILL.md`` is absent.
    """
    try:
        from bmad_assist.skills import get_bundled_skill_md
    except Exception:  # pragma: no cover — defensive only
        logger.debug("bmad_assist.skills package unavailable", exc_info=True)
        return None

    bundled = get_bundled_skill_md(skill_id)
    if bundled is None:
        return None
    return bundled.resolve()


def _warn_on_checksum_mismatch(skill_id: str, claude_path: Path, agents_path: Path) -> None:
    """Log a warning if the two mirror files disagree on content."""
    try:
        claude_digest = hashlib.sha256(claude_path.read_bytes()).hexdigest()
        agents_digest = hashlib.sha256(agents_path.read_bytes()).hexdigest()
    except OSError as exc:
        logger.warning(
            "could not compare %s mirrors for checksum: %s", skill_id, exc
        )
        return
    if claude_digest != agents_digest:
        logger.warning(
            "skill '%s' differs between %s and %s (sha256 %s vs %s); "
            "preferring .claude/skills",
            skill_id,
            claude_path,
            agents_path,
            claude_digest,
            agents_digest,
        )
