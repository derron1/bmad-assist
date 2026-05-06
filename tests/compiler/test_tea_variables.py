"""Tests for TEA variable resolution.

Tests the TEA-specific variable resolution including:
- Knowledge index resolution
- TEA config flag loading
- Next step file resolution
- Integration with step chain compilation
"""

from pathlib import Path

import pytest


class TestResolveKnowledgeIndex:
    """Tests for resolve_knowledge_index()."""

    def test_default_location(self, tmp_path: Path) -> None:
        """Should find knowledge index in default location."""
        from bmad_assist.compiler.variables.tea import resolve_knowledge_index

        # Create default location
        tea_dir = tmp_path / "_bmad/tea/testarch"
        tea_dir.mkdir(parents=True)
        index_file = tea_dir / "tea-index.csv"
        index_file.write_text("header,data\nrow1,value1")

        result = resolve_knowledge_index(tmp_path)

        assert result == str(index_file)

    def test_fallback_location(self, tmp_path: Path) -> None:
        """Should find knowledge index in fallback location."""
        from bmad_assist.compiler.variables.tea import resolve_knowledge_index

        # Create fallback location (not default)
        bmm_dir = tmp_path / "_bmad/bmm/testarch"
        bmm_dir.mkdir(parents=True)
        index_file = bmm_dir / "tea-index.csv"
        index_file.write_text("header,data")

        result = resolve_knowledge_index(tmp_path)

        assert result == str(index_file)

    def test_explicit_path_found(self, tmp_path: Path) -> None:
        """Should use explicit path when provided."""
        from bmad_assist.compiler.variables.tea import resolve_knowledge_index

        # Create custom location
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        index_file = custom_dir / "my-index.csv"
        index_file.write_text("header,data")

        result = resolve_knowledge_index(tmp_path, "custom/my-index.csv")

        assert result == str(index_file)

    def test_explicit_path_not_found(self, tmp_path: Path) -> None:
        """Should return None for missing explicit path."""
        from bmad_assist.compiler.variables.tea import resolve_knowledge_index

        result = resolve_knowledge_index(tmp_path, "nonexistent/path.csv")

        assert result is None

    def test_no_project_index_falls_back_to_bundled(self, tmp_path: Path) -> None:
        """Phase 4: with no project install, the bundled fallback is returned."""
        from bmad_assist.compiler.variables.tea import resolve_knowledge_index

        result = resolve_knowledge_index(tmp_path)

        # The bundled tea-index.csv ships with the package and is now the
        # final fallback (previously this returned None — see Phase 4
        # for the bundled-self-contained rationale).
        assert result is not None
        assert result.endswith("tea-index.csv")


class TestResolveTeaConfigFlags:
    """Tests for resolve_tea_config_flags()."""

    def test_default_values_no_config(self, tmp_path: Path) -> None:
        """Should return defaults when no module.yaml exists."""
        from bmad_assist.compiler.variables.tea import resolve_tea_config_flags

        result = resolve_tea_config_flags(tmp_path)

        assert result["tea_use_playwright_utils"] is True
        assert result["tea_use_mcp_enhancements"] is True

    def test_loads_from_module_yaml(self, tmp_path: Path) -> None:
        """Should load flags from module.yaml."""
        from bmad_assist.compiler.variables.tea import resolve_tea_config_flags

        # Create module.yaml with dict format
        tea_dir = tmp_path / "_bmad/tea"
        tea_dir.mkdir(parents=True)
        module_yaml = tea_dir / "module.yaml"
        module_yaml.write_text(
            """
tea_use_playwright_utils:
  default: false
tea_use_mcp_enhancements:
  default: true
"""
        )

        result = resolve_tea_config_flags(tmp_path)

        assert result["tea_use_playwright_utils"] is False
        assert result["tea_use_mcp_enhancements"] is True

    def test_loads_simple_values(self, tmp_path: Path) -> None:
        """Should handle simple boolean values in module.yaml."""
        from bmad_assist.compiler.variables.tea import resolve_tea_config_flags

        tea_dir = tmp_path / "_bmad/tea"
        tea_dir.mkdir(parents=True)
        module_yaml = tea_dir / "module.yaml"
        module_yaml.write_text(
            """
tea_use_playwright_utils: false
tea_use_mcp_enhancements: false
"""
        )

        result = resolve_tea_config_flags(tmp_path)

        assert result["tea_use_playwright_utils"] is False
        assert result["tea_use_mcp_enhancements"] is False

    def test_handles_invalid_yaml(self, tmp_path: Path) -> None:
        """Should return defaults for invalid YAML."""
        from bmad_assist.compiler.variables.tea import resolve_tea_config_flags

        tea_dir = tmp_path / "_bmad/tea"
        tea_dir.mkdir(parents=True)
        module_yaml = tea_dir / "module.yaml"
        module_yaml.write_text("not: valid: yaml: [")

        result = resolve_tea_config_flags(tmp_path)

        # Should use defaults on error
        assert result["tea_use_playwright_utils"] is True
        assert result["tea_use_mcp_enhancements"] is True


class TestResolveNextStepFile:
    """Tests for resolve_next_step_file()."""

    def test_resolves_relative_path(self, tmp_path: Path) -> None:
        """Should resolve relative path to absolute."""
        from bmad_assist.compiler.variables.tea import resolve_next_step_file

        steps_dir = tmp_path / "steps-c"
        steps_dir.mkdir()
        current_step = steps_dir / "step-01.md"
        current_step.write_text("")

        result = resolve_next_step_file("./step-02.md", current_step)

        assert result == str(steps_dir / "step-02.md")

    def test_none_input(self) -> None:
        """Should return None for None input."""
        from bmad_assist.compiler.variables.tea import resolve_next_step_file

        result = resolve_next_step_file(None, Path("/some/path.md"))

        assert result is None

    def test_empty_string_input(self) -> None:
        """Should return None for empty string."""
        from bmad_assist.compiler.variables.tea import resolve_next_step_file

        result = resolve_next_step_file("", Path("/some/path.md"))

        assert result is None

    def test_resolves_skill_root_token(self, tmp_path: Path) -> None:
        """Should substitute {skill-root} to nearest ancestor SKILL.md dir."""
        from bmad_assist.compiler.variables.tea import resolve_next_step_file

        skill_dir = tmp_path / "bmad-fake-skill"
        steps_dir = skill_dir / "steps-c"
        steps_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Fake\n")
        current_step = steps_dir / "step-01.md"
        current_step.write_text("")

        result = resolve_next_step_file("{skill-root}/steps-c/step-02.md", current_step)

        assert result == str(steps_dir / "step-02.md")

    def test_unresolved_skill_root_raises(self, tmp_path: Path) -> None:
        """Should raise CompilerError when {skill-root} can't be resolved."""
        from bmad_assist.compiler.variables.tea import resolve_next_step_file
        from bmad_assist.core.exceptions import CompilerError

        # No SKILL.md anywhere
        loose_dir = tmp_path / "loose"
        loose_dir.mkdir()
        current_step = loose_dir / "step-01.md"
        current_step.write_text("")

        with pytest.raises(CompilerError) as exc_info:
            resolve_next_step_file("{skill-root}/steps-c/step-02.md", current_step)

        assert "unresolved token" in str(exc_info.value).lower()


class TestResolveTeaVariables:
    """Tests for resolve_tea_variables()."""

    def test_adds_knowledge_index(self, tmp_path: Path) -> None:
        """Should add knowledgeIndex to resolved vars."""
        from bmad_assist.compiler.variables.tea import resolve_tea_variables

        # Create knowledge index
        tea_dir = tmp_path / "_bmad/tea/testarch"
        tea_dir.mkdir(parents=True)
        index_file = tea_dir / "tea-index.csv"
        index_file.write_text("data")

        resolved: dict = {}
        resolve_tea_variables(resolved, tmp_path)

        assert "knowledgeIndex" in resolved
        assert resolved["knowledgeIndex"] == str(index_file)

    def test_adds_config_flags(self, tmp_path: Path) -> None:
        """Should add TEA config flags to resolved vars."""
        from bmad_assist.compiler.variables.tea import resolve_tea_variables

        resolved: dict = {}
        resolve_tea_variables(resolved, tmp_path)

        assert resolved["tea_use_playwright_utils"] is True
        assert resolved["tea_use_mcp_enhancements"] is True

    def test_does_not_override_existing(self, tmp_path: Path) -> None:
        """Should not override existing values."""
        from bmad_assist.compiler.variables.tea import resolve_tea_variables

        resolved = {"tea_use_playwright_utils": False}
        resolve_tea_variables(resolved, tmp_path)

        assert resolved["tea_use_playwright_utils"] is False

    def test_explicit_knowledge_index(self, tmp_path: Path) -> None:
        """Should use explicit knowledge index path."""
        from bmad_assist.compiler.variables.tea import resolve_tea_variables

        # Create custom index
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        index_file = custom_dir / "index.csv"
        index_file.write_text("data")

        resolved: dict = {}
        resolve_tea_variables(resolved, tmp_path, "custom/index.csv")

        assert resolved["knowledgeIndex"] == str(index_file)


class TestCompileStepChain:
    """Tests for compile_step_chain() with variable resolution."""

    def test_basic_compilation(self, tmp_path: Path) -> None:
        """Should compile step chain with variable substitution."""
        from bmad_assist.compiler.step_chain import compile_step_chain

        # Create step directory
        steps_dir = tmp_path / "steps-c"
        steps_dir.mkdir()

        # Create step with variable
        step1 = steps_dir / "step-01.md"
        step1.write_text(
            """---
name: step-01
description: First step
---
Project root is: {project_root}
Epic number is: {epic_num}
"""
        )

        resolved = {
            "project_root": str(tmp_path),
            "epic_num": 25,
        }

        content, context_files = compile_step_chain(step1, resolved, tmp_path)

        assert f"Project root is: {tmp_path}" in content
        assert "Epic number is: 25" in content
        assert "<!-- STEP: step-01 -->" in content

    def test_includes_knowledge_index_in_context(self, tmp_path: Path) -> None:
        """Should include knowledge index in context files."""
        from bmad_assist.compiler.step_chain import compile_step_chain

        # Create knowledge index
        tea_dir = tmp_path / "_bmad/tea/testarch"
        tea_dir.mkdir(parents=True)
        index_file = tea_dir / "tea-index.csv"
        index_file.write_text("header,data")

        # Create step
        steps_dir = tmp_path / "steps-c"
        steps_dir.mkdir()
        step1 = steps_dir / "step-01.md"
        step1.write_text(
            """---
name: step-01
---
Content here
"""
        )

        resolved: dict = {}
        content, context_files = compile_step_chain(step1, resolved, tmp_path)

        assert str(index_file) in context_files
        assert resolved.get("knowledgeIndex") == str(index_file)

    def test_explicit_knowledge_index_in_step(self, tmp_path: Path) -> None:
        """Should use knowledgeIndex from step frontmatter."""
        from bmad_assist.compiler.step_chain import compile_step_chain

        # Create custom index
        custom_dir = tmp_path / "docs"
        custom_dir.mkdir()
        index_file = custom_dir / "custom-index.csv"
        index_file.write_text("data")

        # Create step with explicit knowledgeIndex
        steps_dir = tmp_path / "steps-c"
        steps_dir.mkdir()
        step1 = steps_dir / "step-01.md"
        step1.write_text(
            """---
name: step-01
knowledgeIndex: docs/custom-index.csv
---
Use index: {knowledgeIndex}
"""
        )

        resolved: dict = {}
        content, context_files = compile_step_chain(step1, resolved, tmp_path)

        assert str(index_file) in context_files
        assert str(index_file) in content

    def test_resolves_tea_config_flags(self, tmp_path: Path) -> None:
        """Should resolve TEA config flags in content."""
        from bmad_assist.compiler.step_chain import compile_step_chain

        # Create step
        steps_dir = tmp_path / "steps-c"
        steps_dir.mkdir()
        step1 = steps_dir / "step-01.md"
        step1.write_text(
            """---
name: step-01
---
Playwright utils: {tea_use_playwright_utils}
MCP enhancements: {tea_use_mcp_enhancements}
"""
        )

        resolved: dict = {}
        content, _ = compile_step_chain(step1, resolved, tmp_path)

        assert "Playwright utils: True" in content
        assert "MCP enhancements: True" in content

    def test_chain_with_multiple_steps(self, tmp_path: Path) -> None:
        """Should compile chain of multiple steps."""
        from bmad_assist.compiler.step_chain import compile_step_chain

        steps_dir = tmp_path / "steps-c"
        steps_dir.mkdir()

        # Create step 1
        step1 = steps_dir / "step-01.md"
        step1.write_text(
            """---
name: step-01
nextStepFile: ./step-02.md
---
Step 1 content with {var1}
"""
        )

        # Create step 2
        step2 = steps_dir / "step-02.md"
        step2.write_text(
            """---
name: step-02
---
Step 2 content with {var2}
"""
        )

        resolved = {"var1": "value1", "var2": "value2"}
        content, _ = compile_step_chain(step1, resolved, tmp_path)

        assert "<!-- STEP: step-01 -->" in content
        assert "Step 1 content with value1" in content
        assert "<!-- STEP: step-02 -->" in content
        assert "Step 2 content with value2" in content


class TestLoadTeaModuleConfig:
    """Tests for load_tea_module_config().

    The function pre-resolves the TEA module path config (test_artifacts
    + FUTURE-marked siblings) so the compile-time `<tea-paths>` block
    can give the LLM concrete paths instead of unresolved tokens.
    """

    def _write_yaml(self, project_root: Path, body: str) -> None:
        tea_dir = project_root / "_bmad" / "tea"
        tea_dir.mkdir(parents=True, exist_ok=True)
        (tea_dir / "config.yaml").write_text(body)

    def _write_toml(self, project_root: Path, body: str) -> None:
        bmad_dir = project_root / "_bmad"
        bmad_dir.mkdir(parents=True, exist_ok=True)
        (bmad_dir / "config.toml").write_text(body)

    def test_load_tea_module_config_from_yaml(self, tmp_path: Path) -> None:
        """YAML is the primary source. Returned dict preserves declaration keys."""
        from bmad_assist.compiler.variables.tea import load_tea_module_config

        self._write_yaml(
            tmp_path,
            'test_artifacts: "{project-root}/_bmad-output/test-artifacts"\n'
            "test_design_output: _bmad-output/test-artifacts/test-design\n",
        )

        result = load_tea_module_config(tmp_path)

        assert "test_artifacts" in result
        assert result["test_artifacts"] == str(
            tmp_path.resolve() / "_bmad-output" / "test-artifacts"
        )
        assert result["test_design_output"] == "_bmad-output/test-artifacts/test-design"

    def test_load_tea_module_config_falls_back_to_toml(self, tmp_path: Path) -> None:
        """Without YAML, the TOML `[modules.tea]` section is used."""
        from bmad_assist.compiler.variables.tea import load_tea_module_config

        self._write_toml(
            tmp_path,
            "[core]\n"
            'output_folder = "{project-root}/_bmad-output"\n'
            "\n"
            "[modules.tea]\n"
            'test_artifacts = "{project-root}/_bmad-output/test-artifacts"\n'
            'trace_output = "_bmad-output/test-artifacts/traceability"\n',
        )

        result = load_tea_module_config(tmp_path)

        assert result["test_artifacts"] == str(
            tmp_path.resolve() / "_bmad-output" / "test-artifacts"
        )
        assert result["trace_output"] == "_bmad-output/test-artifacts/traceability"

    def test_load_tea_module_config_returns_empty_when_neither(self, tmp_path: Path) -> None:
        """Fresh project with no _bmad install -> empty dict (no injection)."""
        from bmad_assist.compiler.variables.tea import load_tea_module_config

        assert load_tea_module_config(tmp_path) == {}

    def test_load_tea_module_config_resolves_project_root_token(self, tmp_path: Path) -> None:
        """`{project-root}` is replaced with the canonical project_root path."""
        from bmad_assist.compiler.variables.tea import load_tea_module_config

        self._write_yaml(tmp_path, 'test_artifacts: "{project-root}/some/where"\n')

        result = load_tea_module_config(tmp_path)

        assert "{project-root}" not in result["test_artifacts"]
        assert result["test_artifacts"].endswith("/some/where")
        assert str(tmp_path.resolve()) in result["test_artifacts"]

    def test_load_tea_module_config_resolves_output_folder_token(self, tmp_path: Path) -> None:
        """`{output_folder}` is replaced from `[core].output_folder` (TOML)."""
        from bmad_assist.compiler.variables.tea import load_tea_module_config

        # `{output_folder}` substitution is sourced from the TOML
        # `[core]` section; YAML configs typically inline the path so
        # this lives on the TOML branch.
        self._write_toml(
            tmp_path,
            "[core]\n"
            'output_folder = "{project-root}/_bmad-output"\n'
            "\n"
            "[modules.tea]\n"
            'test_artifacts = "{output_folder}/test-artifacts"\n',
        )

        result = load_tea_module_config(tmp_path)

        assert "{output_folder}" not in result["test_artifacts"]
        assert result["test_artifacts"] == str(
            tmp_path.resolve() / "_bmad-output" / "test-artifacts"
        )

    def test_load_tea_module_config_skips_unknown_and_empty_keys(self, tmp_path: Path) -> None:
        """Only TEA_PATH_KEYS are emitted; non-string / blank values dropped."""
        from bmad_assist.compiler.variables.tea import load_tea_module_config

        self._write_yaml(
            tmp_path,
            'test_artifacts: "{project-root}/_bmad-output/test-artifacts"\n'
            "test_design_output: ''\n"  # blank -> dropped
            "tea_use_playwright_utils: true\n"  # not a path key -> dropped
            "risk_threshold: p1\n",  # not a path key -> dropped
        )

        result = load_tea_module_config(tmp_path)

        assert list(result.keys()) == ["test_artifacts"]

    def test_load_tea_module_config_yaml_wins_over_toml(self, tmp_path: Path) -> None:
        """When both exist, YAML is the source of truth (matches BMAD v6.4+)."""
        from bmad_assist.compiler.variables.tea import load_tea_module_config

        self._write_yaml(
            tmp_path,
            'test_artifacts: "{project-root}/from-yaml"\n',
        )
        self._write_toml(
            tmp_path,
            '[modules.tea]\ntest_artifacts = "{project-root}/from-toml"\n',
        )

        result = load_tea_module_config(tmp_path)

        assert result["test_artifacts"].endswith("/from-yaml")
