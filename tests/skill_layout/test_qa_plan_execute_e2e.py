"""End-to-end tests for the Phase 3.5 ``bmad-qa-plan-execute`` compiler.

The legacy ``QaPlanExecuteCompiler.validate_context`` enforces that
the per-epic test plan exists on disk before compile. The skill-layout
compiler delegates to that check; the fixture seeds a minimal test
plan so compile succeeds.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-qa-plan-execute"
PATCH_FILE = REPO_ROOT / ".bmad-assist" / "patches" / "qa-plan-execute.patch.yaml"


def _install_skill(project_root: Path) -> Path:
    target = project_root / ".claude" / "skills" / "bmad-qa-plan-execute"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_project_artifacts(project_root: Path) -> Path:
    """Seed a minimal test plan so the legacy precondition check passes."""
    output_folder = project_root / "out"
    qa = output_folder / "qa-artifacts"
    plans = qa / "test-plans"
    plans.mkdir(parents=True)
    (plans / "epic-10-e2e-plan.md").write_text(
        "# E2E Test Plan - Epic 10\n\n"
        "## Setup\n```bash\nexport PROJECT_ROOT=\"$(pwd)\"\n```\n\n"
        "## Master Checklist\n\n| ID | Test | Cat | Status |\n|----|------|-----|--------|\n"
        "| E10-A01 | Smoke | A | pending |\n\n"
        "## Category A Tests\n\n### E10-A01: Smoke\n```bash\necho ok\n```\n\n"
        "<!-- QA_PLAN_END -->\n"
    )
    return output_folder


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Project root."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    output_folder = project_root / "out"
    return CompilerContext(
        project_root=project_root,
        output_folder=output_folder,
        project_knowledge=output_folder,
        resolved_variables={"epic_num": 10},
    )


# --------------------------------------------------------------------------- #
# Core proof-of-architecture                                                  #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCompileE2E:
    """Tests for SkillLayoutCompileE2E."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """Test compile returns well shaped compiled workflow."""
        result = compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-qa-plan-execute"
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        assert "<workflow>" in body
        assert "<step n=" in body

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """Test skill layout new routes through new path."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-qa-plan-execute",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadQaPlanExecuteCompiler"

    def test_legacy_workflow_name_routes_to_new_compiler_when_layout_new(
        self, project_root: Path
    ) -> None:
        """Test legacy workflow name routes to new compiler when layout new."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "qa-plan-execute", skill_layout="new", project_root=project_root
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadQaPlanExecuteCompiler"

    def test_compiled_body_preserves_non_interactive_signal(self, project_root: Path) -> None:
        """SKILL.md authoring keeps the NON-INTERACTIVE / auto-continue rules."""
        result = compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        # The patch's must_contain doesn't enforce this directly, but
        # the SKILL.md authors keep the headless-mode rule explicit
        # because regression here would silently re-enable interactive
        # prompts in CI.
        assert "non-interactive" in body.lower() or "NON-INTERACTIVE" in body


# --------------------------------------------------------------------------- #
# Cache lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCacheLifecycle:
    """Tests for SkillLayoutCacheLifecycle."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-qa-plan-execute.tpl.xml",
            cache_dir / "bmad-qa-plan-execute.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """Test cache is written on first compile."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()
        compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )
        assert cache_path.is_file()
        assert meta_path.is_file()

    def test_cache_invalidates_when_customize_toml_changes(self, project_root: Path) -> None:
        """Test cache invalidates when customize toml changes."""
        _, meta_path = self._cache_paths(project_root)
        compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )
        original_meta = meta_path.read_text(encoding="utf-8")
        customize = project_root / ".claude" / "skills" / "bmad-qa-plan-execute" / "customize.toml"
        customize.write_text(
            customize.read_text(encoding="utf-8") + "\n# tweak\n",
            encoding="utf-8",
        )
        compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )
        new_meta = meta_path.read_text(encoding="utf-8")
        assert new_meta != original_meta

    def test_cache_meta_records_skill_layout_mode_and_hashes(self, project_root: Path) -> None:
        """Test cache meta records skill layout mode and hashes."""
        import yaml

        _, meta_path = self._cache_paths(project_root)
        compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-qa-plan-execute"
        assert meta["skill_md_hash"]
        assert meta["customize_toml_hash"]
        # qa-plan-execute HAS a patch — patch_hash should be non-empty.
        assert meta["patch_hash"], "patch_hash must be populated when patch exists"
        assert meta["transform_mode"] in {"llm", "regex_only"}


# --------------------------------------------------------------------------- #
# LLM transform integration                                                   #
# --------------------------------------------------------------------------- #


def _stub_master_provider_config():
    from bmad_assist.core.config.models.providers import MasterProviderConfig

    return MasterProviderConfig(
        provider="claude-subprocess",
        model="opus",
        model_name="opus-test",
    )


def _stub_config_with_master(master) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        providers=SimpleNamespace(master=master, multi=[]),
        timeouts=None,
        timeout=300,
        phase_models=None,
    )


class TestSkillLayoutLLMTransforms:
    """Tests for SkillLayoutLLMTransforms."""

    def test_compile_with_provider_invokes_apply_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """qa-plan-execute's patch DOES have transforms — verify wiring fires."""
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_qa_plan_execute as skill_mod

        captured: dict[str, object] = {}

        def fake_apply(
            *,
            content: str,
            transforms: list[str],
            provider_config,
            config,
            phase_name: str,
            workflow_label: str,
            **_: object,
        ):
            captured.update(
                content=content,
                transforms=list(transforms),
                phase_name=phase_name,
                workflow_label=workflow_label,
            )
            # Patch's must_contain rules expect Markdown step headers
            # (the legacy compiler reads instructions.md). Include them
            # alongside the XML envelope so both XML well-formedness
            # and patch must_contain assertions pass.
            transformed = (
                "<workflow>\n"
                "<critical>NON-INTERACTIVE MODE: skip every &lt;ask&gt; / &lt;confirm&gt;.</critical>\n"
                '<step n="1" goal="Initialize">'
                "<action>## Step 1 Initialize and Gather Input. Parse embedded test plan and execute.</action>"
                "</step>\n"
                '<step n="4" goal="Execute Cat A">'
                "<action>## Step 4: Execute Category A — run each Cat A test.</action>"
                "</step>\n"
                '<step n="6" goal="Persist results">'
                "<action>## Step 6: Generate Results — emit YAML and summary.</action>"
                "</step>\n"
                "</workflow>"
            )
            results = [
                TransformResult(success=True, transform_index=i) for i in range(len(transforms))
            ]
            return transformed, results

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", fake_apply)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )

        compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )

        assert captured, "apply_llm_transforms was never called"
        assert captured["workflow_label"] == "bmad-qa-plan-execute"
        assert captured["phase_name"] == "qa_plan_execute"
        assert isinstance(captured["transforms"], list)
        assert len(captured["transforms"]) >= 1
        assert "<workflow>" in captured["content"]

    def test_compile_without_provider_skips_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test compile without provider skips llm transforms."""
        import logging

        from bmad_assist.compiler.skills import bmad_qa_plan_execute as skill_mod

        called = {"hit": False}

        def boom(**kwargs):
            called["hit"] = True
            raise AssertionError("apply_llm_transforms should not be called")

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", boom)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )

        caplog.set_level(logging.INFO, logger=skill_mod.logger.name)
        compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(project_root),
            skill_layout="new",
        )
        assert called["hit"] is False
        assert any(
            "Skipping LLM transforms" in rec.message
            for rec in caplog.records
            if rec.name == skill_mod.logger.name
        )
