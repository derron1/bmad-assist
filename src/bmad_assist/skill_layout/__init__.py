"""BMAD v6.4+ "skill" layout reader.

This package provides primitives for reading and resolving the BMAD
v6.4+ skill format:

* :func:`parse_skill` — turn ``SKILL.md`` into a structured document.
* :func:`resolve_customization` / :func:`resolve_central_config` — the
  pure-Python merger that mirrors ``_bmad/scripts/*.py``.
* :func:`read_skill_manifest` — index ``skill-manifest.csv``.
* :func:`find_skill` — runtime probe for ``SKILL.md`` files.
"""

from .chain_validator import ChainValidationError, validate_skill_chains
from .errors import (
    MalformedSkill,
    ManifestError,
    ResolverError,
    SkillLayoutError,
    SkillNotFound,
)
from .manifest import read_skill_manifest
from .parser import parse_skill
from .probe import find_bundled_skill, find_skill
from .resolver import (
    deep_merge,
    extract_key,
    merge_arrays,
    resolve_central_config,
    resolve_customization,
)
from .types import Layout, ManifestSkill, SkillDocument, SkillFrontmatter
from .variable_resolver import resolve_skill_variables

__all__ = [
    "ChainValidationError",
    "Layout",
    "MalformedSkill",
    "ManifestError",
    "ManifestSkill",
    "ResolverError",
    "SkillDocument",
    "SkillFrontmatter",
    "SkillLayoutError",
    "SkillNotFound",
    "deep_merge",
    "extract_key",
    "find_bundled_skill",
    "find_skill",
    "merge_arrays",
    "parse_skill",
    "read_skill_manifest",
    "resolve_central_config",
    "resolve_customization",
    "resolve_skill_variables",
    "validate_skill_chains",
]
