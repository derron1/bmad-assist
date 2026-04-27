"""End-to-end tests for the Phase 3.2-B ``bmad-testarch-ci`` compiler.

Mirror of :mod:`tests.skill_layout.test_testarch_atdd_e2e` for the
testarch-ci skill-layout consumer. Step-file architecture (no inline
``<workflow>`` envelope; we assert on the outer
``<compiled-workflow>`` envelope and the workflow's section title).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-testarch-ci"
PATCH_FILE = REPO_ROOT / ".bmad-assist" / "patches" / "testarch-ci.patch.yaml"


def _install_skill(project_root: Path) -> Path:
    """Mirror the bundled skill into the project's ``.claude/skills/`` directory."""
    target = project_root / ".claude" / "skills" / "bmad-testarch-ci"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_project_artifacts(project_root: Path) -> Path:
    """Populate the tmp project with the docs the compiler expects."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context.\n")
    (docs / "prd.md").write_text("# PRD\n")
    (docs / "architecture.md").write_text("# Architecture\n")
    (docs / "epics").mkdir()
    (docs / "epics" / "epic-10.md").write_text("# Epic 10\n")
    sprint_dir = docs / "sprint-artifacts"
    sprint_dir.mkdir()
    (sprint_dir / "sprint-status.yaml").write_text("development_status:\n  10-1-foo: ready-for-dev\n")
    (sprint_dir / "10-1-foo.md").write_text("# Story 10.1\n## Status\nready-for-dev\n")
    return docs


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Set up an isolated project tree with the v6.4+ skill installed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    """Build a minimal compiler context pointing at the seeded docs/."""
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        # CI is project-/epic-level; story_num optional.
        resolved_variables={"epic_num": 10},
    )


class TestSkillLayoutCompileE2E:
    """End-to-end compile through the Phase 3.2-B testarch-ci path."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """``compile_workflow`` returns a non-empty, well-shaped result."""
        result = compile_workflow(
            "bmad-testarch-ci",
            _make_context(project_root),
            skill_layout="new",
        )
        assert result.workflow_name == "bmad-testarch-ci"
        body = result.context
        assert body
        assert "<compiled-workflow>" in body
        assert "CI/CD Pipeline Setup" in body or "pipeline" in body.lower()

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """``skill_layout="new"`` picks the skill-layout compiler."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-testarch-ci", skill_layout="new", project_root=project_root
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadTestarchCiCompiler"

    def test_legacy_workflow_name_routes_to_new_compiler_when_layout_new(
        self, project_root: Path
    ) -> None:
        """Legacy un-prefixed name must also route to the new compiler."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "testarch-ci", skill_layout="new", project_root=project_root
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadTestarchCiCompiler"


class TestSkillLayoutCacheLifecycle:
    """Cache write + invalidation behaviour for the testarch-ci skill path."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        """Return the (body, meta) cache file paths for this skill."""
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-testarch-ci.tpl.xml",
            cache_dir / "bmad-testarch-ci.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """First compile writes ``.tpl.xml`` and ``.tpl.xml.meta.yaml``."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()
        compile_workflow("bmad-testarch-ci", _make_context(project_root), skill_layout="new")
        assert cache_path.is_file()
        assert meta_path.is_file()

    def test_cache_invalidates_when_customize_toml_changes(self, project_root: Path) -> None:
        """Mutating ``customize.toml`` rewrites the cache meta."""
        _, meta_path = self._cache_paths(project_root)
        compile_workflow("bmad-testarch-ci", _make_context(project_root), skill_layout="new")
        original_meta = meta_path.read_text(encoding="utf-8")
        customize = project_root / ".claude" / "skills" / "bmad-testarch-ci" / "customize.toml"
        customize.write_text(
            customize.read_text(encoding="utf-8") + "\n# tweak\n",
            encoding="utf-8",
        )
        compile_workflow("bmad-testarch-ci", _make_context(project_root), skill_layout="new")
        assert meta_path.read_text(encoding="utf-8") != original_meta

    def test_cache_meta_records_skill_layout_mode_and_hashes(self, project_root: Path) -> None:
        """Cache meta records the layout mode + content hashes."""
        import yaml

        _, meta_path = self._cache_paths(project_root)
        compile_workflow("bmad-testarch-ci", _make_context(project_root), skill_layout="new")
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-testarch-ci"
        assert meta["skill_md_hash"]
        assert meta["customize_toml_hash"]
        assert "patch_hash" in meta
        assert meta["transform_mode"] in {"llm", "regex_only"}


def _stub_master_provider_config():
    """Return a minimal MasterProviderConfig stub for tests."""
    from bmad_assist.core.config.models.providers import MasterProviderConfig

    return MasterProviderConfig(provider="claude-subprocess", model="opus", model_name="opus-test")


def _stub_config_with_master(master) -> object:
    """Build a minimal Config-like stub. testarch is single-LLM."""
    from types import SimpleNamespace

    return SimpleNamespace(
        providers=SimpleNamespace(master=master),
        timeouts=None,
        timeout=300,
        phase_models=None,
    )


class TestSkillLayoutLLMTransforms:
    """LLM-transform wiring + cache mode invalidation for testarch-ci."""

    def test_compile_with_provider_invokes_apply_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Compiler routes through ``apply_llm_transforms`` when a master provider is set."""
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_testarch_ci as skill_mod

        captured: dict[str, object] = {}

        def fake_apply(*, content, transforms, provider_config, config, phase_name, workflow_label, **_):
            captured.update(
                content=content,
                transforms=list(transforms),
                phase_name=phase_name,
                workflow_label=workflow_label,
            )
            # Patch must_contain: /[Cc][Ii]|[Pp]ipeline/ + /[Tt]est/.
            transformed = (
                "<workflow>\n"
                '<step n="1" goal="Pipeline">'
                "<action>Scaffold a CI pipeline that runs the test suite.</action>"
                "</step>\n"
                "</workflow>"
            )
            return transformed, [
                TransformResult(success=True, transform_index=i) for i in range(len(transforms))
            ]

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", fake_apply)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )
        compile_workflow("bmad-testarch-ci", _make_context(project_root), skill_layout="new")
        assert captured
        assert captured["workflow_label"] == "bmad-testarch-ci"
        assert captured["phase_name"] == "testarch_ci"
        assert isinstance(captured["transforms"], list)
        assert len(captured["transforms"]) >= 1
        assert "CI/CD Pipeline Setup" in captured["content"]

    def test_compile_without_provider_skips_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Skip ``apply_llm_transforms`` when ``providers.master`` is None."""
        import logging

        from bmad_assist.compiler.skills import bmad_testarch_ci as skill_mod

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
        compile_workflow("bmad-testarch-ci", _make_context(project_root), skill_layout="new")
        assert called["hit"] is False
        assert any(
            "Skipping LLM transforms" in rec.message
            for rec in caplog.records
            if rec.name == skill_mod.logger.name
        )
