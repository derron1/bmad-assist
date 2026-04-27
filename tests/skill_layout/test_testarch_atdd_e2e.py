"""End-to-end tests for the Phase 3.2-B ``bmad-testarch-atdd`` compiler.

Mirror of :mod:`tests.skill_layout.test_code_review_e2e` for the
testarch-atdd skill-layout consumer. Stands up a synthetic BMAD v6.4+
project with a ready-for-dev story, invokes :func:`compile_workflow`
through the new path (``skill_layout="new"``), and checks that the
resulting :class:`CompiledWorkflow` is well-shaped, the cache is
written, and the LLM-transform path is invoked / skipped per provider
availability.

bmad-testarch-atdd uses BMAD v6.4+ step-file architecture (no inline
``<workflow>`` envelope; step files load just-in-time at runtime). We
therefore assert on the compiler's outer ``<compiled-workflow>``
envelope and on the workflow's section title — not on ``<workflow>``
or ``<step n=...>`` markers.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-testarch-atdd"
PATCH_FILE = REPO_ROOT / ".bmad-assist" / "patches" / "testarch-atdd.patch.yaml"


def _install_skill(project_root: Path) -> Path:
    """Mirror the bundled skill into ``.claude/skills/bmad-testarch-atdd/``."""
    target = project_root / ".claude" / "skills" / "bmad-testarch-atdd"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    # Ensure the v6.4+ marker file exists so detect_layout() returns
    # "new" against this project root.
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")
    return target


def _seed_project_artifacts(project_root: Path) -> Path:
    """Populate the tmp project with the docs the testarch-atdd compiler expects."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text(
        "# Project Context\n\nMinimal context for testarch-atdd tests.\n"
    )
    (docs / "prd.md").write_text("# PRD\n\nProject requirements.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    epics_dir = docs / "epics"
    epics_dir.mkdir()
    (epics_dir / "epic-10-test.md").write_text(
        "# Epic 10: Test Epic\n\n## Story 10.1: Initial Setup\n\nContent.\n"
    )
    sprint_dir = docs / "sprint-artifacts"
    sprint_dir.mkdir()
    (sprint_dir / "sprint-status.yaml").write_text(
        "development_status:\n  10-1-initial-setup: ready-for-dev\n"
    )
    (sprint_dir / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\nready-for-dev\n\n"
        "## Acceptance Criteria\n\n- [ ] AC1\n\n"
        "## Tasks/Subtasks\n\n- [ ] Task 1\n\n"
        "## Dev Notes\n\nMinimal notes.\n"
    )
    return docs


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Set up an isolated project tree with the v6.4+ testarch-atdd skill installed."""
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
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


# --------------------------------------------------------------------------- #
# Core proof-of-architecture                                                  #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCompileE2E:
    """End-to-end compile through the Phase 3.2-B testarch-atdd path."""

    def test_compile_returns_well_shaped_compiled_workflow(self, project_root: Path) -> None:
        """``compile_workflow`` returns a non-empty, well-shaped result."""
        result = compile_workflow(
            "bmad-testarch-atdd",
            _make_context(project_root),
            skill_layout="new",
        )

        assert result.workflow_name == "bmad-testarch-atdd"
        body = result.context
        assert body, "compiled workflow body must be non-empty"
        # bmad-testarch-atdd uses BMAD v6.4+ step-file architecture: the
        # SKILL.md body contains routing pointers to step files and is
        # wrapped by the compiler in <compiled-workflow>. There is NO
        # inline <workflow>...</workflow> envelope to assert against.
        assert "<compiled-workflow>" in body
        assert (
            "Acceptance Test-Driven Development" in body
            or "ATDD" in body
            or "atdd" in body.lower()
        )

    def test_skill_layout_new_routes_through_new_path(self, project_root: Path) -> None:
        """``skill_layout="new"`` picks the testarch-atdd skill-layout compiler."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "bmad-testarch-atdd",
            skill_layout="new",
            project_root=project_root,
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadTestarchAtddCompiler"

    def test_legacy_workflow_name_routes_to_new_compiler_when_layout_new(
        self, project_root: Path
    ) -> None:
        """``testarch-atdd`` (legacy name) must also route to the new path."""
        from bmad_assist.compiler.core import get_workflow_compiler

        compiler = get_workflow_compiler(
            "testarch-atdd", skill_layout="new", project_root=project_root
        )
        assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")
        assert type(compiler).__name__ == "BmadTestarchAtddCompiler"


# --------------------------------------------------------------------------- #
# Cache lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestSkillLayoutCacheLifecycle:
    """Cache write + invalidation behaviour for the testarch-atdd skill path."""

    def _cache_paths(self, project_root: Path) -> tuple[Path, Path]:
        cache_dir = project_root / ".bmad-assist" / "cache" / "skills"
        return (
            cache_dir / "bmad-testarch-atdd.tpl.xml",
            cache_dir / "bmad-testarch-atdd.tpl.xml.meta.yaml",
        )

    def test_cache_is_written_on_first_compile(self, project_root: Path) -> None:
        """First compile writes ``.tpl.xml`` and ``.tpl.xml.meta.yaml``."""
        cache_path, meta_path = self._cache_paths(project_root)
        assert not cache_path.exists()

        compile_workflow(
            "bmad-testarch-atdd",
            _make_context(project_root),
            skill_layout="new",
        )

        assert cache_path.is_file(), f"expected cache at {cache_path}"
        assert meta_path.is_file(), f"expected cache meta at {meta_path}"

    def test_cache_invalidates_when_customize_toml_changes(self, project_root: Path) -> None:
        """Mutating ``customize.toml`` rewrites the cache meta."""
        cache_path, meta_path = self._cache_paths(project_root)

        compile_workflow(
            "bmad-testarch-atdd",
            _make_context(project_root),
            skill_layout="new",
        )
        original_meta = meta_path.read_text(encoding="utf-8")

        customize = project_root / ".claude" / "skills" / "bmad-testarch-atdd" / "customize.toml"
        customize.write_text(
            customize.read_text(encoding="utf-8") + "\n# tweak to invalidate cache\n",
            encoding="utf-8",
        )

        compile_workflow(
            "bmad-testarch-atdd",
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
            "bmad-testarch-atdd",
            _make_context(project_root),
            skill_layout="new",
        )

        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assert meta["skill_layout_mode"] == "new"
        assert meta["skill_id"] == "bmad-testarch-atdd"
        assert meta["skill_md_hash"], "skill_md_hash must be populated"
        assert meta["customize_toml_hash"], "customize_toml_hash must be populated"
        assert "patch_hash" in meta
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
    """Build a minimal Config-like stub.

    testarch workflows are single-LLM (not in MULTI_LLM_PHASES), so we
    use the simple shape without ``multi=[]``. See dev-story's
    ``_stub_config_with_master`` for the canonical single-LLM shape.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        providers=SimpleNamespace(master=master),
        timeouts=None,
        timeout=300,
        phase_models=None,
    )


class TestSkillLayoutLLMTransforms:
    """LLM-transform wiring + cache mode invalidation for testarch-atdd."""

    def test_compile_with_provider_invokes_apply_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Compiler routes through ``apply_llm_transforms`` when a master provider is set."""
        from bmad_assist.compiler.patching.types import TransformResult
        from bmad_assist.compiler.skills import bmad_testarch_atdd as skill_mod

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
            # Echo back a body that:
            #  * carries a <workflow>...</workflow> envelope so
            #    validate_workflow_xml passes (the validator runs
            #    whenever transforms ran, regardless of whether the
            #    SKILL.md source had inline <workflow> tags),
            #  * satisfies the patch's must_contain rules
            #    (/[Aa]cceptance/ + /[Tt]est/),
            #  * does NOT contain {installed_path} (must_not_contain).
            transformed = (
                "<workflow>\n"
                '<step n="1" goal="Acceptance">'
                "<action>Generate failing acceptance tests for the story.</action>"
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
            "bmad-testarch-atdd",
            _make_context(project_root),
            skill_layout="new",
        )

        assert captured, "apply_llm_transforms was never called"
        assert captured["workflow_label"] == "bmad-testarch-atdd"
        assert captured["phase_name"] == "testarch_atdd"
        assert isinstance(captured["transforms"], list)
        assert len(captured["transforms"]) >= 1
        # The body that was passed in must be the variable-substituted
        # SKILL.md body — i.e. it must reference the workflow's section
        # title. Unlike dev-story / create-story it does NOT have an
        # inline <workflow> envelope (v6.4+ step-file architecture
        # loads micro-step files at runtime).
        assert "Acceptance Test-Driven Development" in captured["content"]

    def test_compile_without_provider_skips_llm_transforms(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Skip ``apply_llm_transforms`` when ``providers.master`` is None."""
        import logging

        from bmad_assist.compiler.skills import bmad_testarch_atdd as skill_mod

        called = {"hit": False}

        def boom(**kwargs):
            called["hit"] = True
            raise AssertionError("apply_llm_transforms should not be called")

        monkeypatch.setattr(skill_mod, "apply_llm_transforms", boom)
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )

        # The base class emits the skip log under the subclass module's
        # logger name (see _subclass_logger), which is exactly what
        # ``skill_mod.logger.name`` resolves to.
        caplog.set_level(logging.INFO, logger=skill_mod.logger.name)
        compile_workflow(
            "bmad-testarch-atdd",
            _make_context(project_root),
            skill_layout="new",
        )
        assert called["hit"] is False
        assert any(
            "Skipping LLM transforms" in rec.message
            for rec in caplog.records
            if rec.name == skill_mod.logger.name
        )
