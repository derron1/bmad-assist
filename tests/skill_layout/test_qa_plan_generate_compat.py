"""Side-by-side: legacy vs skill-layout qa-plan-generate compile.

The legacy compiler reads ``instructions.md`` (Markdown) — distinct
from validate-story's XML source. The skill-layout pipeline operates
on the SKILL.md body and is source-extension agnostic, but this test
verifies that the bounded-difference invariants still hold when the
underlying source format differs.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-qa-plan-generate"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "qa-plan-generate"


def _seed_artifacts(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "prd.md").write_text("# PRD\n\nFR-1: example.\nNFR-1: example.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    epics = docs / "epics"
    epics.mkdir()
    (epics / "epic-10.md").write_text(
        "# Epic 10: Test Epic\n\nObjectives.\n\n## Story 10.1\n\nContent.\n"
    )
    (docs / "ux-elements.md").write_text(
        "# UX Elements\n\n- `[data-testid=\"main-panel\"]`\n- `[data-testid=\"submit-btn\"]`\n"
    )
    impl_stories = project_root / "implementation-artifacts" / "stories"
    impl_stories.mkdir(parents=True)
    (impl_stories / "10-1-initial-setup.md").write_text(
        "# Story 10.1\n\n## Acceptance Criteria\n- AC-1: example\n"
    )
    return docs


def _install_new_layout(project_root: Path) -> None:
    target = project_root / ".claude" / "skills" / "bmad-qa-plan-generate"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    target = project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / "qa-plan-generate"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(LEGACY_WORKFLOW, target)
    config_dir = project_root / "_bmad" / "bmm"
    config = config_dir / "config.yaml"
    docs = project_root / "docs"
    config.write_text(
        f"project_name: test\n"
        f"output_folder: '{project_root}'\n"
        f"planning_artifacts: '{docs}'\n"
        f"implementation_artifacts: '{project_root}/implementation-artifacts'\n"
    )
    return target


@pytest.fixture
def dual_project(tmp_path: Path) -> Path:
    """Dual project."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed_artifacts(proj)
    _install_old_layout(proj)
    _install_new_layout(proj)
    return proj


def _make_context(project_root: Path) -> CompilerContext:
    return CompilerContext(
        project_root=project_root,
        output_folder=project_root,
        project_knowledge=project_root / "docs",
        resolved_variables={"epic_num": 10},
    )


# --------------------------------------------------------------------------- #
# Side-by-side                                                                #
# --------------------------------------------------------------------------- #


class TestSideBySideCompatibility:
    """Tests for SideBySideCompatibility."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Test both paths produce compiled workflows."""
        old = compile_workflow("qa-plan-generate", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context
        assert new.context

    def test_both_reference_epic(self, dual_project: Path) -> None:
        """Test both reference epic."""
        old = compile_workflow("qa-plan-generate", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "epic" in body.lower(), f"{label} body should mention epic"

    def test_both_reference_categories(self, dual_project: Path) -> None:
        """Both paths preserve the Category A/B/C classification language."""
        old = compile_workflow("qa-plan-generate", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "Category A" in body, f"{label} body missing Category A signal"
            assert "Category B" in body, f"{label} body missing Category B signal"
            assert "Category C" in body, f"{label} body missing Category C signal"

    def test_new_path_is_workflow_xml_well_formed(self, dual_project: Path) -> None:
        """The new path emits a valid <workflow> envelope (legacy uses MD, no envelope)."""
        new = compile_workflow(
            "bmad-qa-plan-generate",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert "<workflow>" in new.context
        assert "</workflow>" in new.context


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# qa-plan-generate's legacy source is Markdown (~13k); the new
# SKILL.md is XML-wrapped outcome-based markdown (~9k after
# truncation). The diff is large by construction since the source
# formats differ; thresholds below catch unintended regressions.
_DIFF_THRESHOLD = 1500


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


@pytest.mark.parametrize("provider_available", [True, False])
def test_legacy_vs_new_compile_diff_is_bounded(
    provider_available: bool,
    dual_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test legacy vs new compile diff is bounded."""
    import difflib

    if provider_available:
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(_stub_master_provider_config()),
        )
    else:
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )

    old = compile_workflow("qa-plan-generate", _make_context(dual_project), skill_layout="old")
    new = compile_workflow(
        "bmad-qa-plan-generate",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/qa-plan-generate",
            tofile="new/bmad-qa-plan-generate",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= _DIFF_THRESHOLD, (
        f"diff between legacy and new qa-plan-generate exceeds {_DIFF_THRESHOLD} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
