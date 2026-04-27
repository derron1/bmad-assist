"""Tests for ``bmad_assist.skill_layout.parser``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_assist.skill_layout import MalformedSkill, parse_skill

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_minimal_skill_returns_frontmatter() -> None:
    """Frontmatter ``name`` and ``description`` are exposed verbatim."""
    doc = parse_skill(FIXTURES / "minimal_skill" / "SKILL.md")
    assert doc.frontmatter.name == "minimal-skill"
    assert "minimal SKILL.md" in doc.frontmatter.description


def test_parse_minimal_skill_extracts_sections() -> None:
    """Level-2 keys list their level-3 sub-headings in order."""
    doc = parse_skill(FIXTURES / "minimal_skill" / "SKILL.md")
    assert "## On Activation" in doc.sections
    assert "## Conventions" in doc.sections
    assert doc.sections["## On Activation"] == [
        "### Step 1: Greet the User",
        "### Step 2: Exit",
    ]


def test_parse_minimal_skill_extracts_activation_block() -> None:
    """The ``## On Activation`` block is captured up to the next level-2 heading."""
    doc = parse_skill(FIXTURES / "minimal_skill" / "SKILL.md")
    assert doc.activation_block is not None
    assert doc.activation_block.startswith("## On Activation")
    assert "Step 1: Greet the User" in doc.activation_block
    assert "## Conventions" not in doc.activation_block


def test_parse_minimal_skill_no_workflow_xml() -> None:
    """Documents without a ``<workflow>`` block report ``None``."""
    doc = parse_skill(FIXTURES / "minimal_skill" / "SKILL.md")
    assert doc.workflow_xml is None


def test_parse_minimal_skill_collects_variable_refs() -> None:
    """``{token}`` references appear in ``variable_refs`` with braces stripped."""
    doc = parse_skill(FIXTURES / "minimal_skill" / "SKILL.md")
    assert "skill-root" in doc.variable_refs
    assert "project-root" in doc.variable_refs


def test_parse_minimal_skill_resolves_skill_root() -> None:
    """``skill_root`` is the absolute parent directory of ``SKILL.md``."""
    path = FIXTURES / "minimal_skill" / "SKILL.md"
    doc = parse_skill(path)
    assert doc.skill_root == path.parent.resolve()
    assert doc.skill_root.is_absolute()


def test_parse_skill_with_workflow_xml() -> None:
    """Inline ``<workflow>`` blocks are captured raw, including the tags."""
    doc = parse_skill(FIXTURES / "skill_with_workflow_xml" / "SKILL.md")
    assert doc.workflow_xml is not None
    assert doc.workflow_xml.startswith("<workflow>")
    assert doc.workflow_xml.endswith("</workflow>")
    assert "say hi" in doc.workflow_xml
    assert "skill-root" in doc.variable_refs
    assert "user_name" in doc.variable_refs


def test_parse_missing_file_raises() -> None:
    """A non-existent ``SKILL.md`` raises :class:`MalformedSkill`."""
    with pytest.raises(MalformedSkill, match="not found"):
        parse_skill(FIXTURES / "does_not_exist" / "SKILL.md")


def test_parse_missing_frontmatter_raises() -> None:
    """A SKILL.md without a YAML frontmatter block raises."""
    with pytest.raises(MalformedSkill, match="frontmatter"):
        parse_skill(FIXTURES / "skill_no_frontmatter" / "SKILL.md")


def test_parse_malformed_yaml_raises() -> None:
    """Unparseable YAML in the frontmatter raises."""
    with pytest.raises(MalformedSkill, match="frontmatter"):
        parse_skill(FIXTURES / "skill_malformed_yaml" / "SKILL.md")


def test_parse_missing_required_field_raises(tmp_path: Path) -> None:
    """Missing ``name`` raises with a message that names the field."""
    skill_dir = tmp_path / "no_name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: missing name field\n---\n\n# Body\n",
        encoding="utf-8",
    )
    with pytest.raises(MalformedSkill, match="'name'"):
        parse_skill(skill_dir / "SKILL.md")


def test_parse_missing_description_raises(tmp_path: Path) -> None:
    """Missing ``description`` raises with a message that names the field."""
    skill_dir = tmp_path / "no_description"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bare\n---\n\n# Body\n",
        encoding="utf-8",
    )
    with pytest.raises(MalformedSkill, match="'description'"):
        parse_skill(skill_dir / "SKILL.md")


def test_parse_real_create_story_skill() -> None:
    """Smoke test against the bundled ``bmad-create-story`` SKILL.md."""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / ".claude" / "skills" / "bmad-create-story" / "SKILL.md"
    if not path.exists():
        pytest.skip("bundled bmad-create-story SKILL.md not present")
    doc = parse_skill(path)
    assert doc.frontmatter.name == "bmad-create-story"
    assert doc.workflow_xml is not None
    assert "{skill-root}" not in doc.variable_refs  # braces stripped
    assert "skill-root" in doc.variable_refs
    assert doc.activation_block is not None


def test_parse_tea_style_skill_no_workflow_xml() -> None:
    """TEA-style SKILL.md delegates to ``steps-*/`` files; no inline XML."""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / ".claude" / "skills" / "bmad-testarch-atdd" / "SKILL.md"
    if not path.exists():
        pytest.skip("bundled bmad-testarch-atdd SKILL.md not present")
    doc = parse_skill(path)
    assert doc.frontmatter.name == "bmad-testarch-atdd"
    assert doc.workflow_xml is None
    assert doc.activation_block is not None
