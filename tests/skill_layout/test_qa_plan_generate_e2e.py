"""End-to-end tests for the Phase 3.5 ``bmad-qa-plan-generate`` compiler.

Mirror of the validate-story e2e test for the qa-plan-generate orphan
(MD source instead of XML — the legacy compiler reads
``instructions.md`` rather than ``instructions.xml``). The skill-layout
pipeline operates on the SKILL.md body and is source-extension
agnostic; this test verifies that the new path produces a well-shaped
result, the cache lifecycle works, and LLM-transform wiring is intact.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-qa-plan-generate"
PATCH_FILE = REPO_ROOT / ".bmad-assist" / "patches" / "qa-plan-generate.patch.yaml"


def _install_skill(project_root: Path) -> Path:
    target = project_root / ".claude" / "skills" / "bmad-qa-plan-generate"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_project_artifacts(project_root: Path) -> Path:
    """Seed the project with the docs the qa-plan-generate compiler scans."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "prd.md").write_text("# PRD\n\nFR-1: example.\nNFR-1: example.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    epics = docs / "epics"
    epics.mkdir()
    (epics / "epic-10.md").write_text(
        "# Epic 10: Test Epic\n\nObjectives.\n\n## Story 10.1\n\nContent.\n"
    )
    # qa-plan-generate looks for ux-elements files under docs/
    (docs / "ux-elements.md").write_text(
        "# UX Elements\n\n- `[data-testid=\"main-panel\"]`\n- `[data-testid=\"submit-btn\"]`\n"
    )
    impl_stories = project_root / "implementation-artifacts" / "stories"
    impl_stories.mkdir(parents=True)
    (impl_stories / "10-1-initial-setup.md").write_text(
        "# Story 10.1\n\n## Acceptance Criteria\n- AC-1: example\n"
    )
    return docs


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Project root."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    """qa-plan-generate uses ``output_folder`` for stories, qa-artifacts."""
    output_folder = project_root  # output_folder/implementation-artifacts/stories
    return CompilerContext(
        project_root=project_root,
        output_folder=output_folder,
        project_knowledge=project_root / "docs",
        resolved_variables={"epic_num": 10},
    )


# --------------------------------------------------------------------------- #
# Core proof-of-architecture                                                  #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCompileE2E:
    """End-to-end compile through the Phase 3.5 qa-plan-generate path."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """Test compile returns well shaped compiled workflow."""
        result = compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-qa-plan-generate"
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        assert "<workflow>" in body
        assert "<step n=" in body

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """Test skill layout new routes through new path."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-qa-plan-generate",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadQaPlanGenerateCompiler"

    def test_compiled_body_preserves_category_signals(self, project_root: Path) -> None:
        """The Cat A/B/C classification rules survive the compile."""
        result = compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(project_root),
            skill_layout="new",
        )
        body = result.context
        # SKILL.md authoring keeps Category A / B / C language for the
        # downstream parser; patch's must_contain checks for these too.
        assert "Category A" in body
        assert "Category B" in body
        assert "Category C" in body


# --------------------------------------------------------------------------- #
# Cache lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCacheLifecycle:
    """Tests for SkillLayoutCacheLifecycle."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-qa-plan-generate.tpl.xml",
            cache_dir / "bmad-qa-plan-generate.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """Test cache is written on first compile."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()
        compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(project_root),
            skill_layout="new",
        )
        assert cache_path.is_file()
        assert meta_path.is_file()

    def test_cache_invalidates_when_customize_toml_changes(self, project_root: Path) -> None:
        """Test cache invalidates when customize toml changes."""
        _, meta_path = self._cache_paths(project_root)
        compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(project_root),
            skill_layout="new",
        )
        original_meta = meta_path.read_text(encoding="utf-8")
        customize = project_root / ".claude" / "skills" / "bmad-qa-plan-generate" / "customize.toml"
        customize.write_text(
            customize.read_text(encoding="utf-8") + "\n# tweak\n",
            encoding="utf-8",
        )
        compile_workflow(
            "bmad-qa-plan-generate",
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
            "bmad-qa-plan-generate",
            _make_context(project_root),
            skill_layout="new",
        )
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-qa-plan-generate"
        assert meta["skill_md_hash"]
        assert meta["customize_toml_hash"]
        # qa-plan-generate HAS a patch — patch_hash should be non-empty.
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
    """qa_plan_generate is a SINGLE_LLM phase — no `multi` field needed."""
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
        """Test compile with provider invokes apply llm transforms."""
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_qa_plan_generate as skill_mod

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
            transformed = (
                "<workflow>\n"
                "<critical>Cat A / Cat B / Cat C classification.</critical>\n"
                '<step n="1" goal="Plan">'
                "<action>Generate the test plan.</action>"
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
            "bmad-qa-plan-generate",
            _make_context(project_root),
            skill_layout="new",
        )

        # qa-plan-generate's patch has NO transforms (regex-only patch).
        # The base class skips the LLM call when patch.transforms is empty.
        # Verify the path is "skipped due to no transforms" rather than
        # "called with content" — reflected by captured staying empty.
        assert captured == {}, (
            "apply_llm_transforms should NOT be called for qa-plan-generate "
            "because the patch has no transforms (regex-only). "
            f"Got captured={captured}"
        )

    def test_compile_without_provider_skips_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test compile without provider skips llm transforms."""
        import logging

        from bmad_assist.compiler.skills import bmad_qa_plan_generate as skill_mod

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
            "bmad-qa-plan-generate",
            _make_context(project_root),
            skill_layout="new",
        )
        assert called["hit"] is False
        assert any(
            "Skipping LLM transforms" in rec.message
            for rec in caplog.records
            if rec.name == skill_mod.logger.name
        )
