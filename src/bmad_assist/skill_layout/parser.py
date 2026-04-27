"""Parse ``SKILL.md`` files into :class:`SkillDocument` instances.

The parser is intentionally lightweight: it extracts frontmatter,
discovers section headings, captures the ``## On Activation`` block
and any inline ``<workflow>`` XML, and collects ``{variable}``
references for later resolution. It does *not* descend into the
workflow XML — that dialect is handled by the existing compiler.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .errors import MalformedSkill
from .types import SkillDocument, SkillFrontmatter

# Frontmatter is delimited by --- on its own line at the start of the file.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)

# A section heading: ## or ### at the start of a line, then space, then text.
# The heading is preserved verbatim (including the marker) when stored as a key.
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)

# The "On Activation" block runs from its `## On Activation` heading to the
# next `## ` heading (or end-of-file). Captured greedily across newlines.
_ACTIVATION_RE = re.compile(
    r"^(##\s+On Activation\s*)$(.*?)(?=^##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)

# `<workflow>...</workflow>` may span many lines; we capture the entire
# block including the tags.
_WORKFLOW_XML_RE = re.compile(r"<workflow\b[^>]*>.*?</workflow>", re.DOTALL)

# Variable references look like {token} where token starts with a letter
# and uses ASCII alphanumerics, dot, underscore, or hyphen. This is
# deliberately narrower than Jinja so we don't match `{{double}}` braces
# or arbitrary text.
_VARIABLE_REF_RE = re.compile(r"\{([a-zA-Z][a-zA-Z0-9._-]*)\}")


def parse_skill(skill_md_path: Path) -> SkillDocument:
    """Parse a ``SKILL.md`` file.

    Args:
        skill_md_path: Path to the ``SKILL.md`` file. Need not be absolute.

    Returns:
        A :class:`SkillDocument` capturing the parsed structure.

    Raises:
        MalformedSkill: If the file does not exist, lacks a YAML
            frontmatter block, has unparseable YAML, or is missing
            either ``name`` or ``description``.

    """
    if not skill_md_path.exists():
        raise MalformedSkill(f"SKILL.md not found: {skill_md_path}")

    try:
        raw_markdown = skill_md_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MalformedSkill(f"Failed to read {skill_md_path}: {exc}") from exc

    match = _FRONTMATTER_RE.match(raw_markdown)
    if match is None:
        raise MalformedSkill(
            f"{skill_md_path}: missing YAML frontmatter delimited by '---' lines"
        )

    frontmatter_text, body = match.group(1), match.group(2)

    try:
        frontmatter_data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise MalformedSkill(f"{skill_md_path}: failed to parse frontmatter: {exc}") from exc

    if not isinstance(frontmatter_data, dict):
        raise MalformedSkill(
            f"{skill_md_path}: frontmatter must be a YAML mapping, "
            f"got {type(frontmatter_data).__name__}"
        )

    name = frontmatter_data.get("name")
    description = frontmatter_data.get("description")
    if not isinstance(name, str) or not name.strip():
        raise MalformedSkill(f"{skill_md_path}: frontmatter missing required string field 'name'")
    if not isinstance(description, str) or not description.strip():
        raise MalformedSkill(
            f"{skill_md_path}: frontmatter missing required string field 'description'"
        )

    sections = _extract_sections(body)
    activation_block = _extract_activation_block(body)
    workflow_xml = _extract_workflow_xml(body)
    variable_refs = frozenset(_VARIABLE_REF_RE.findall(body))

    return SkillDocument(
        frontmatter=SkillFrontmatter(name=name, description=description),
        sections=sections,
        activation_block=activation_block,
        workflow_xml=workflow_xml,
        variable_refs=variable_refs,
        raw_markdown=raw_markdown,
        skill_root=skill_md_path.parent.resolve(),
    )


def _extract_sections(body: str) -> dict[str, list[str]]:
    """Build a heading-tree of the body.

    Keys are level-2 (``##``) headings preserved verbatim with the
    marker. Each value is the ordered list of level-3 (``###``)
    sub-headings that appear under that section. Standalone level-3
    headings that appear before any level-2 heading land under a
    sentinel key of ``""`` to avoid losing them silently.
    """
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    for marker, text in _HEADING_RE.findall(body):
        heading = f"{marker} {text}"
        if marker == "##":
            sections.setdefault(heading, [])
            current_key = heading
        else:  # marker == "###"
            if current_key is None:
                sections.setdefault("", []).append(heading)
            else:
                sections[current_key].append(heading)
    return sections


def _extract_activation_block(body: str) -> str | None:
    """Return the raw ``## On Activation`` section text, or ``None``.

    The returned string includes the heading line itself and runs up to
    (but not including) the next level-2 heading. Trailing whitespace is
    preserved as-is so callers can detect accidental empty sections.
    """
    match = _ACTIVATION_RE.search(body)
    if match is None:
        return None
    return match.group(1) + match.group(2)


def _extract_workflow_xml(body: str) -> str | None:
    """Return the raw ``<workflow>...</workflow>`` block if present."""
    match = _WORKFLOW_XML_RE.search(body)
    if match is None:
        return None
    return match.group(0)
