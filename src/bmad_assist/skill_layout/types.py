"""Frozen dataclasses describing the BMAD v6.4+ skill layout.

These types are the public data surface of the ``skill_layout`` package:
they describe parsed SKILL.md documents, manifest entries, and the
discrete layout flavor in use. All dataclasses are frozen so they can
be safely shared across the compilation/loop pipeline that will
consume them in Phase 2+.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Layout = Literal["new", "old"]
"""Distinguishes the BMAD v6.4+ "skill" layout from the legacy "workflow" layout."""


@dataclass(frozen=True)
class SkillFrontmatter:
    """YAML frontmatter at the top of a ``SKILL.md`` file.

    Attributes:
        name: The skill's stable identifier (e.g. ``"bmad-create-story"``).
        description: Human-readable description used by the harness when
            offering this skill to the user.

    """

    name: str
    description: str


@dataclass(frozen=True)
class ManifestSkill:
    """A row from ``_bmad/_config/skill-manifest.csv``.

    Only the four required columns are exposed as attributes; extra
    columns are tolerated by the reader but discarded.

    Attributes:
        skill_id: Stable identifier (e.g. ``"bmad-create-story"``).
        module: Source module the skill ships with (e.g. ``"bmm"``,
            ``"tea"``, ``"core"``).
        canonical_path: Path string from the manifest. This is the
            *fallback* probe target — runtime probes prefer the
            ``.claude/skills`` and ``.agents/skills`` mirrors.
        description: Human-readable description from the manifest.

    """

    skill_id: str
    module: str
    canonical_path: str
    description: str


@dataclass(frozen=True)
class SkillDocument:
    """Parsed ``SKILL.md`` document.

    Attributes:
        frontmatter: Parsed YAML frontmatter.
        sections: Mapping from each ``##``/``###`` heading line (preserved
            verbatim including the marker) to the list of sub-section
            heading lines that follow it.
        activation_block: Raw markdown of the ``## On Activation`` section,
            or ``None`` if the document does not declare one.
        workflow_xml: Raw text of the ``<workflow>...</workflow>`` block
            (including the surrounding tags) if present in the body, else
            ``None``.
        variable_refs: Set of all variable references in the body — each
            entry is the *bare* name (braces stripped). For example, a
            body containing ``{skill-root}`` contributes the entry
            ``"skill-root"``.
        raw_markdown: The complete file contents, byte-for-byte.
        skill_root: Absolute path to the directory containing
            ``SKILL.md``.

    """

    frontmatter: SkillFrontmatter
    sections: dict[str, list[str]]
    activation_block: str | None
    workflow_xml: str | None
    variable_refs: frozenset[str]
    raw_markdown: str
    skill_root: Path
