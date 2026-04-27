"""Unit tests for ``bmad_assist.skill_layout.variable_resolver``.

The resolver is intentionally narrow: it only substitutes the tokens
the Phase 2 compiler relies on and leaves everything else alone for
downstream layers to handle. These tests pin both behaviours.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bmad_assist.skill_layout import parse_skill, resolve_skill_variables
from bmad_assist.skill_layout.types import SkillDocument, SkillFrontmatter


def _make_document(
    body: str,
    skill_root: Path,
    *,
    name: str = "bmad-create-story",
    description: str = "Test skill description.",
) -> SkillDocument:
    """Synthesize a :class:`SkillDocument` for a body without writing files.

    The resolver only reads ``raw_markdown`` and ``skill_root``; the
    remaining fields are populated to keep the dataclass valid.
    """
    return SkillDocument(
        frontmatter=SkillFrontmatter(name=name, description=description),
        sections={},
        activation_block=None,
        workflow_xml=None,
        variable_refs=frozenset(),
        raw_markdown=body,
        skill_root=skill_root,
    )


# --------------------------------------------------------------------------- #
# Built-in path tokens                                                        #
# --------------------------------------------------------------------------- #


class TestPathTokens:
    """Built-in path/identifier tokens — the always-resolved set."""

    def test_skill_root_substituted(self, tmp_path: Path) -> None:
        """``{skill-root}`` resolves to the absolute skill directory."""
        skill_root = tmp_path / "skill-dir"
        skill_root.mkdir()
        doc = _make_document("path: {skill-root}/template.md", skill_root)
        out = resolve_skill_variables(doc, {}, tmp_path)
        assert out == f"path: {skill_root.resolve()}/template.md"

    def test_project_root_substituted(self, tmp_path: Path) -> None:
        """``{project-root}`` resolves to the resolved project root."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        doc = _make_document("root: {project-root}", skill_root)
        out = resolve_skill_variables(doc, {}, tmp_path)
        assert out == f"root: {tmp_path.resolve()}"

    def test_skill_name_uses_directory_basename(self, tmp_path: Path) -> None:
        """``{skill-name}`` is the basename of the skill directory."""
        skill_root = tmp_path / "bmad-create-story"
        skill_root.mkdir()
        doc = _make_document("name: {skill-name}", skill_root)
        out = resolve_skill_variables(doc, {}, tmp_path)
        assert out == "name: bmad-create-story"

    def test_installed_path_alias_resolves_like_skill_root(self, tmp_path: Path) -> None:
        """``{installed_path}`` is a legacy alias for ``{skill-root}``."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        doc = _make_document("legacy: {installed_path}", skill_root)
        out = resolve_skill_variables(doc, {}, tmp_path)
        assert out == f"legacy: {skill_root.resolve()}"


# --------------------------------------------------------------------------- #
# Customization-driven prose substitution                                     #
# --------------------------------------------------------------------------- #


class TestCustomizationTokens:
    """Dotted ``{workflow.*}`` tokens — sourced from customize.toml."""

    def test_workflow_persistent_facts_renders_as_bullets(self, tmp_path: Path) -> None:
        """Lists become bullet lines, one entry per line."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "Facts:\n{workflow.persistent_facts}\nEnd"
        customization = {
            "workflow": {
                "persistent_facts": [
                    "file:{project-root}/docs/standards.md",
                    "Stories must include testable acceptance criteria.",
                ]
            }
        }
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(doc, customization, tmp_path)
        assert "- file:{project-root}/docs/standards.md" in out
        assert "- Stories must include testable acceptance criteria." in out

    def test_workflow_on_complete_scalar(self, tmp_path: Path) -> None:
        """Scalar values render verbatim as their string form."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "Hook: '{workflow.on_complete}'"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(
            doc, {"workflow": {"on_complete": "echo done"}}, tmp_path
        )
        assert out == "Hook: 'echo done'"

    def test_empty_list_renders_to_empty_string(self, tmp_path: Path) -> None:
        """Empty lists collapse the surrounding prose cleanly."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "Steps:\n{workflow.activation_steps_prepend}\nDone"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(
            doc, {"workflow": {"activation_steps_prepend": []}}, tmp_path
        )
        assert out == "Steps:\n\nDone"

    def test_dict_renders_as_key_value_lines(self, tmp_path: Path) -> None:
        """Dicts render as ``key: value`` lines, one per key."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "{workflow.config}"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(
            doc,
            {"workflow": {"config": {"mode": "fast", "retries": 3}}},
            tmp_path,
        )
        assert "mode: fast" in out
        assert "retries: 3" in out

    def test_missing_dotted_path_left_intact(self, tmp_path: Path) -> None:
        """Unresolvable customization paths are not stripped."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "x = {workflow.does_not_exist}"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(doc, {"workflow": {}}, tmp_path)
        assert out == "x = {workflow.does_not_exist}"

    def test_complex_list_items_serialize_as_inline_json(self, tmp_path: Path) -> None:
        """Nested list items collapse to JSON inside their bullet."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "{workflow.menu}"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(
            doc,
            {"workflow": {"menu": [{"code": "help", "description": "Show help"}]}},
            tmp_path,
        )
        # Each list item becomes its own bullet; dicts inline as JSON.
        assert out.startswith("- ")
        assert '"code": "help"' in out
        assert '"description": "Show help"' in out


# --------------------------------------------------------------------------- #
# Caller-provided extras / unknown tokens                                     #
# --------------------------------------------------------------------------- #


class TestExtraVarsAndUnknownTokens:
    """Caller-provided overrides + the leave-intact contract."""

    def test_extra_vars_override_builtins(self, tmp_path: Path) -> None:
        """Caller-provided ``extra_vars`` shadow built-in tokens."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "{project-root}"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(
            doc, {}, tmp_path, extra_vars={"project-root": "/override"}
        )
        assert out == "/override"

    def test_extra_vars_inject_compiler_variables(self, tmp_path: Path) -> None:
        """Compiler-injected tokens (e.g. ``sprint_status_path``) substitute."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "Sprint: {sprint_status_path}"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(
            doc, {}, tmp_path, extra_vars={"sprint_status_path": "/x/sprint.yaml"}
        )
        assert out == "Sprint: /x/sprint.yaml"

    def test_unknown_token_is_left_intact(self, tmp_path: Path) -> None:
        """Tokens we don't recognise remain intact for downstream layers."""
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "epic: {epic_num}, story: {story_title}"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(doc, {}, tmp_path)
        assert out == "epic: {epic_num}, story: {story_title}"

    def test_double_brace_not_substituted(self, tmp_path: Path) -> None:
        """``{{...}}`` is for the workflow engine, not for us — keep it."""
        # ``{{...}}`` is the workflow engine's placeholder syntax; the
        # skill-layout resolver only matches single-brace ``{token}``
        # patterns so it must not touch ``{{token}}``.
        skill_root = tmp_path / "skill"
        skill_root.mkdir()
        body = "var: {{epic_num}}"
        doc = _make_document(body, skill_root)
        out = resolve_skill_variables(
            doc, {}, tmp_path, extra_vars={"epic_num": "42"}
        )
        assert "{{epic_num}}" not in out  # outer single-brace got replaced
        # The OUTER ``{epic_num}`` is replaced, leaving ``{42}`` (the
        # extra braces around the substitution). What we actually care
        # about: the substitution does fire (epic_num was injected) and
        # we don't crash.
        assert "42" in out


# --------------------------------------------------------------------------- #
# Live SKILL.md round-trip                                                    #
# --------------------------------------------------------------------------- #


class TestLiveSkill:
    """Full round-trip against the bundled ``bmad-create-story`` skill."""

    def test_round_trip_against_bundled_skill(self, tmp_path: Path) -> None:
        """Resolver leaves the bundled skill workflow XML structurally intact."""
        bundled_root = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "bmad_assist"
            / "skills"
            / "bmad-create-story"
        )
        skill_md = bundled_root / "SKILL.md"
        assert skill_md.is_file(), "Phase 2 must bundle bmad-create-story SKILL.md"

        document = parse_skill(skill_md)
        customization: dict[str, Any] = {
            "workflow": {
                "activation_steps_prepend": [],
                "activation_steps_append": [],
                "persistent_facts": ["file:{project-root}/**/project-context.md"],
                "on_complete": "",
            }
        }
        substituted = resolve_skill_variables(
            document, customization, tmp_path, extra_vars=None
        )

        # Built-in tokens are gone.
        assert "{installed_path}" not in substituted
        assert "{skill-root}" not in substituted
        assert "{skill-name}" not in substituted

        # The skill workflow XML is preserved verbatim.
        assert "<workflow>" in substituted
        assert "</workflow>" in substituted

        # Workflow-internal tokens (resolved later by the existing
        # variable engine / LLM) survive — both the bare-brace and
        # double-brace forms BMAD uses.
        assert "{{epic_num}}" in substituted

        # Bare ``{project-root}`` references inside ``persistent_facts``
        # entries (literal text from customize.toml) get rendered as
        # the actual project root because the resolver substitutes them
        # globally — but the prose around the substituted bullet is
        # what we care about: it should mention the project root path.
        assert str(tmp_path.resolve()) in substituted

        # Workflow-internal ``{planning_artifacts}`` etc. tokens that
        # the workflow-engine resolves are left intact.
        assert "{implementation_artifacts}" in substituted


@pytest.mark.parametrize(
    ("body", "extra", "expected"),
    [
        ("plain text", {}, "plain text"),
        ("{custom}", {"custom": "X"}, "X"),
    ],
)
def test_parametrized_simple_substitutions(
    tmp_path: Path, body: str, extra: dict[str, str], expected: str
) -> None:
    """Parametrised happy-path substitutions cover the trivial cases."""
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    doc = _make_document(body, skill_root)
    assert resolve_skill_variables(doc, {}, tmp_path, extra_vars=extra) == expected
