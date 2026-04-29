"""End-to-end tests for the Phase 3.5 ``bmad-validate-story`` compiler.

Mirror of :mod:`tests.skill_layout.test_dev_story_e2e` for the
validate-story orphan. Stands up a synthetic BMAD v6.4+ project with
a ready-for-validation story, invokes :func:`compile_workflow` through
the new path (``skill_layout="new"``), and checks that the resulting
:class:`CompiledWorkflow` is well-shaped, the cache lifecycle works,
and the LLM-transform path is invoked / skipped per provider
availability.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-validate-story"
PATCH_FILE = REPO_ROOT / ".bmad-assist" / "patches" / "validate-story.patch.yaml"


def _install_patch(project_root: Path) -> Path:
    """Install the project-level patch into the tmp project tree.

    Bundled `default_patches/` no longer ships per-workflow patches
    (Phase 7 inlined those transforms into SKILL.md). E2E tests that
    exercise the LLM-transform branch install a project-level patch
    fixture so :func:`discover_patch` returns a real path.
    """
    patches_dir = project_root / ".bmad-assist" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    target = patches_dir / PATCH_FILE.name
    shutil.copy2(PATCH_FILE, target)
    return target


def _install_skill(project_root: Path) -> Path:
    """Mirror the bundled skill into ``.claude/skills/bmad-validate-story/``."""
    target = project_root / ".claude" / "skills" / "bmad-validate-story"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_project_artifacts(project_root: Path) -> Path:
    """Populate the tmp project with the docs the validate-story compiler expects."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text(
        "# Project Context\n\nMinimal context for validate-story tests.\n"
    )
    (docs / "prd.md").write_text("# PRD\n\nProject requirements.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    epics = docs / "epics"
    epics.mkdir()
    (epics / "epic-10-test.md").write_text(
        "# Epic 10: Test Epic\n\n## Story 10.1: Initial Setup\n\nContent.\n"
    )
    sprint = docs / "sprint-artifacts"
    sprint.mkdir()
    (sprint / "sprint-status.yaml").write_text(
        "development_status:\n  10-1-initial-setup: ready-for-validation\n"
    )
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\nready-for-validation\n\n"
        "## Acceptance Criteria\n\n- [ ] AC1\n\n"
        "## Tasks/Subtasks\n\n- [ ] Task 1\n\n"
        "## Dev Notes\n\nMinimal notes.\n"
    )
    return docs


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Set up an isolated project tree with the v6.4+ validate-story skill installed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    _install_patch(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    """Build a minimal compiler context pointing at the seeded docs/."""
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


# --------------------------------------------------------------------------- #
# Core proof-of-architecture                                                  #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCompileE2E:
    """End-to-end compile through the Phase 3.5 validate-story path."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """``compile_workflow`` returns a non-empty, well-shaped result."""
        result = compile_workflow(
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-validate-story"
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        assert "<workflow>" in body
        assert "<step n=" in body

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """``skill_layout="new"`` picks the validate-story skill-layout compiler."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-validate-story",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadValidateStoryCompiler"

    def test_compiled_body_contains_validation_markers(self, project_root: Path) -> None:
        """The compiled body keeps the validator-output markers required downstream."""
        result = compile_workflow(
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )
        # The patch's regex post-process injects the START/END markers
        # around the embedded output template — they're required by
        # the validate-story patch's must_contain assertions and by
        # the orchestrator's output extractor.
        body = result.context
        assert "VALIDATION_REPORT_START" in body
        assert "VALIDATION_REPORT_END" in body

    def test_compiled_body_preserves_invest_section(self, project_root: Path) -> None:
        """The INVEST quality-gate section survives the compile."""
        result = compile_workflow(
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )
        # The patch's must_contain rule enforces that INVEST/invest
        # appears somewhere in the compiled body — the SKILL.md keeps
        # it explicitly in step 1.
        body = result.context
        assert "INVEST" in body or "invest" in body


# --------------------------------------------------------------------------- #
# Cache lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCacheLifecycle:
    """Cache write + invalidation behaviour for the validate-story skill path."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-validate-story.tpl.xml",
            cache_dir / "bmad-validate-story.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """First compile writes ``.tpl.xml`` and ``.tpl.xml.meta.yaml``."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()

        compile_workflow(
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )

        assert cache_path.is_file(), f"expected cache at {cache_path}"
        assert meta_path.is_file(), f"expected cache meta at {meta_path}"

    def test_cache_invalidates_when_customize_toml_changes(self, project_root: Path) -> None:
        """Mutating ``customize.toml`` rewrites the cache meta."""
        cache_path, meta_path = self._cache_paths(project_root)

        compile_workflow(
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )
        original_meta = meta_path.read_text(encoding="utf-8")

        customize = project_root / ".claude" / "skills" / "bmad-validate-story" / "customize.toml"
        customize.write_text(
            customize.read_text(encoding="utf-8") + "\n# tweak to invalidate cache\n",
            encoding="utf-8",
        )

        compile_workflow(
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )
        new_meta = meta_path.read_text(encoding="utf-8")
        assert new_meta != original_meta, (
            "mutating customize.toml must invalidate the cache and produce a new meta file"
        )

    def test_cache_meta_records_skill_layout_mode_and_hashes(self, project_root: Path) -> None:
        """Cache meta records the layout mode + content hashes."""
        import yaml

        _, meta_path = self._cache_paths(project_root)

        compile_workflow(
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )

        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-validate-story"
        assert meta["skill_md_hash"], "skill_md_hash must be populated"
        assert meta["customize_toml_hash"], "customize_toml_hash must be populated"
        assert "patch_hash" in meta
        # validate-story HAS a patch — patch_hash should be non-empty.
        assert meta["patch_hash"], "patch_hash must be populated when patch exists"
        assert meta["transform_mode"] in {"llm", "regex_only"}


# --------------------------------------------------------------------------- #
# Phase 3.5 — LLM transform integration                                       #
# --------------------------------------------------------------------------- #


def _stub_master_provider_config():
    from bmad_assist.core.config.models.providers import MasterProviderConfig

    return MasterProviderConfig(
        provider="claude-subprocess",
        model="opus",
        model_name="opus-test",
    )


def _stub_config_with_master(master) -> object:
    """Build a minimal Config-like stub.

    validate-story is a MULTI_LLM_PHASES phase, so the base class
    consults ``config.providers.multi`` via :func:`get_phase_provider_config`
    before falling through to ``config.providers.master``. Populate
    ``multi=[]`` so the list-branch in ``_apply_llm_transforms`` falls
    back to the master provider correctly.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        providers=SimpleNamespace(master=master, multi=[]),
        timeouts=None,
        timeout=300,
        phase_models=None,
    )


class TestSkillLayoutLLMTransforms:
    """LLM-transform wiring for validate-story."""

    def test_compile_with_provider_invokes_apply_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Compiler routes through ``apply_llm_transforms`` when a master provider is set."""
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_validate_story as skill_mod

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
            # Echo back a body satisfying the validate-story patch's
            # must_contain rules: <step, <critical, /report/, INVEST.
            transformed = (
                "<workflow>\n"
                "<critical>SCOPE LIMITATION: read-only validator. Generate validation report.</critical>\n"
                '<step n="1" goal="INVEST">'
                "<action>Score INVEST criteria and emit a report.</action>"
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
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )

        assert captured, "apply_llm_transforms was never called"
        assert captured["workflow_label"] == "bmad-validate-story"
        assert captured["phase_name"] == "validate_story"
        assert isinstance(captured["transforms"], list)
        assert len(captured["transforms"]) >= 1
        assert "<workflow>" in captured["content"]

    def test_compile_without_provider_skips_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Skip ``apply_llm_transforms`` when ``providers.master`` is None."""
        import logging

        from bmad_assist.compiler.skills import bmad_validate_story as skill_mod

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
            "bmad-validate-story",
            _make_context(project_root),
            skill_layout="new",
        )
        assert called["hit"] is False
        assert any(
            "Skipping LLM transforms" in rec.message
            for rec in caplog.records
            if rec.name == skill_mod.logger.name
        )
