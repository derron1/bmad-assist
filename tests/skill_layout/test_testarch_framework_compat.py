"""Side-by-side comparison: legacy vs skill-layout testarch-framework compile."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-testarch-framework"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "testarch-framework"


def _seed_artifacts(project_root: Path) -> Path:
    """Populate the project tree with the docs both compilers expect."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n")
    (docs / "prd.md").write_text("# PRD\n")
    (docs / "architecture.md").write_text("# Architecture\n")
    (docs / "epics").mkdir()
    (docs / "epics" / "epic-10.md").write_text("# Epic 10\n")
    sprint = docs / "sprint-artifacts"
    sprint.mkdir()
    (sprint / "sprint-status.yaml").write_text("development_status:\n  10-1-foo: ready-for-dev\n")
    (sprint / "10-1-foo.md").write_text("# Story 10.1\n## Status\nready-for-dev\n")
    return docs


def _install_new_layout(project_root: Path) -> None:
    """Mirror the bundled skill into the project's ``.claude/skills/``."""
    target = project_root / ".claude" / "skills" / "bmad-testarch-framework"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    """Copy the bundled legacy workflow into the project tree."""
    target = project_root / "_bmad" / "bmm" / "workflows" / "testarch" / "framework"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(LEGACY_WORKFLOW, target)
    config_dir = project_root / "_bmad" / "bmm"
    config = config_dir / "config.yaml"
    docs = project_root / "docs"
    config.write_text(
        f"project_name: test\n"
        f"output_folder: '{docs}'\n"
        f"planning_artifacts: '{docs}'\n"
        f"implementation_artifacts: '{docs}'\n"
        f"sprint_artifacts: '{docs / 'sprint-artifacts'}'\n"
    )
    return target


@pytest.fixture
def dual_project(tmp_path: Path) -> Path:
    """Project tree containing BOTH the legacy workflow and the new skill."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_artifacts(proj)
    _install_old_layout(proj)
    _install_new_layout(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    """Build a minimal compiler context pointing at the seeded docs/."""
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10},
    )


class TestSideBySideCompatibility:
    """Bounded-difference invariants between legacy and skill-layout compile."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Both compile paths return non-empty :class:`CompiledWorkflow`s."""
        old = compile_workflow(
            "testarch-framework", _make_context(dual_project), skill_layout="old"
        )
        new = compile_workflow(
            "bmad-testarch-framework", _make_context(dual_project), skill_layout="new"
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context
        assert new.context

    def test_compiled_workflow_envelope_present_on_both_sides(self, dual_project: Path) -> None:
        """Both bodies retain the ``<compiled-workflow>`` outer envelope."""
        old = compile_workflow(
            "testarch-framework", _make_context(dual_project), skill_layout="old"
        )
        new = compile_workflow(
            "bmad-testarch-framework", _make_context(dual_project), skill_layout="new"
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<compiled-workflow>" in body, f"{label} body missing <compiled-workflow>"

    def test_both_embed_project_context(self, dual_project: Path) -> None:
        """Both paths pull the project-context file into the prompt."""
        old = compile_workflow(
            "testarch-framework", _make_context(dual_project), skill_layout="old"
        )
        new = compile_workflow(
            "bmad-testarch-framework", _make_context(dual_project), skill_layout="new"
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "project_context.md" in body or "project-context" in body, (
                f"{label} body should reference project context"
            )


# Diff thresholds — framework's legacy instructions.md is ~38 lines.
# See automate's compat for the rationale; same shape applies here.
_DIFF_THRESHOLD_PROVIDER_TRUE = 1000
_DIFF_THRESHOLD_PROVIDER_FALSE = 1000


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


@pytest.mark.parametrize("provider_available", [True, False])
def test_legacy_vs_new_compile_diff_is_bounded(
    provider_available: bool,
    dual_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded-diff invariant between legacy and skill-layout compile."""
    import difflib

    from bmad_assist.compiler.patching.types import TransformResult
    from bmad_assist.compiler.skills import bmad_testarch_framework as skill_mod

    if provider_available:

        def fake_apply(*, content, transforms, **_):
            # See e2e test for why we add a separate "test suite" line:
            # the patch's post_process collapses "Playwright Test" →
            # "playwright" and would strip the only "Test" token.
            transformed = (
                "<workflow>\n"
                '<step n="1" goal="Framework">'
                "<action>Initialize a Playwright framework with config and fixtures.</action>"
                "<action>Run the test suite to verify the scaffold.</action>"
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
        threshold = _DIFF_THRESHOLD_PROVIDER_TRUE
    else:
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )
        threshold = _DIFF_THRESHOLD_PROVIDER_FALSE

    old = compile_workflow("testarch-framework", _make_context(dual_project), skill_layout="old")
    new = compile_workflow(
        "bmad-testarch-framework", _make_context(dual_project), skill_layout="new"
    )
    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/testarch-framework",
            tofile="new/bmad-testarch-framework",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert len(change_lines) <= threshold, (
        f"diff between legacy and new testarch-framework exceeds {threshold} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)})."
    )
