"""Side-by-side comparison: legacy vs skill-layout validate-story compile.

Mirror of :mod:`tests.skill_layout.test_dev_story_compat` for the
Phase 3.5 validate-story orphan. Stands up a single project that
satisfies both compile paths, runs each, and asserts a bounded set of
structural invariants.

The compat test deliberately avoids byte-for-byte identity — the
Phase 3.5 SKILL.md was authored outcome-based and is materially
different in surface text from the legacy ``instructions.xml``. The
patch's ``post_process`` rules + ``must_contain`` / ``must_not_contain``
assertions enforce the behavioural invariants both paths must share.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-validate-story"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "validate-story"


def _seed_artifacts(project_root: Path) -> Path:
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context for tests.\n")
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


def _install_new_layout(project_root: Path) -> None:
    target = project_root / ".claude" / "skills" / "bmad-validate-story"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    target = project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / "validate-story"
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
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )


# --------------------------------------------------------------------------- #
# Side-by-side                                                                #
# --------------------------------------------------------------------------- #


class TestSideBySideCompatibility:
    """Bounded-difference invariants between legacy and skill-layout validate-story."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Test both paths produce compiled workflows."""
        old = compile_workflow("validate-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-validate-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context, "legacy compile produced empty body"
        assert new.context, "new compile produced empty body"

    def test_workflow_xml_structure_present_on_both_sides(self, dual_project: Path) -> None:
        """Test workflow xml structure present on both sides."""
        old = compile_workflow("validate-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-validate-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<workflow>" in body, f"{label} body missing <workflow>"
            assert "</workflow>" in body, f"{label} body missing </workflow>"

    def test_both_embed_project_context(self, dual_project: Path) -> None:
        """Test both embed project context."""
        old = compile_workflow("validate-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-validate-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "project_context.md" in body or "project-context" in body, (
                f"{label} body should reference project context"
            )

    def test_both_reference_story_target(self, dual_project: Path) -> None:
        """Test both reference story target."""
        old = compile_workflow("validate-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-validate-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "10-1-initial-setup" in body or "Story 10.1" in body, (
                f"{label} body should reference the target story"
            )

    def test_new_path_emits_validation_report_markers(self, dual_project: Path) -> None:
        """The new path injects START/END markers from the SKILL.md body itself.

        Authoring note: the markers live inside the SKILL.md ``<critical>`` block
        (step 5) so they survive variable substitution and end up in the
        compiled body unconditionally — even when the LLM transform path
        is disabled (the conftest-level ``disable_patch_compilation``
        fixture suppresses transforms during tests). The legacy path only
        gets these markers when the patch's LLM transforms succeed, which
        the test environment intentionally blocks; we therefore only
        assert on the new path here.
        """
        new = compile_workflow(
            "bmad-validate-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert "VALIDATION_REPORT_START" in new.context, (
            "new body missing VALIDATION_REPORT_START marker"
        )
        assert "VALIDATION_REPORT_END" in new.context, (
            "new body missing VALIDATION_REPORT_END marker"
        )

    def test_both_preserve_invest_keyword(self, dual_project: Path) -> None:
        """The patch's must_contain rule requires INVEST/invest in both bodies."""
        old = compile_workflow("validate-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-validate-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "INVEST" in body or "invest" in body, (
                f"{label} body should preserve INVEST quality-gate signal"
            )


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# The Phase 3.5 SKILL.md is materially different from the legacy
# instructions.xml (outcome-based rewrite), so the diff is large by
# design. These thresholds protect against accidental regressions
# rather than enforcing identity. If a future change exceeds these
# caps, raise the threshold WITH RATIONALE rather than tightening to
# mask regressions.
_DIFF_THRESHOLD_PROVIDER_TRUE = 1500
_DIFF_THRESHOLD_PROVIDER_FALSE = 1500


def _stub_master_provider_config():
    from bmad_assist.core.config.models.providers import MasterProviderConfig

    return MasterProviderConfig(
        provider="claude-subprocess",
        model="opus",
        model_name="opus-test",
    )


def _stub_config_with_master(master) -> object:
    """Build a minimal Config-like stub.

    validate-story is a MULTI_LLM_PHASES phase, so populate
    ``multi=[]`` to drive the list-branch fallback to master.
    """
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
    """Bounded-diff invariant between legacy and skill-layout validate-story."""
    import difflib

    from bmad_assist.compiler.patching.types import TransformResult
    from bmad_assist.compiler.skills import bmad_validate_story as skill_mod

    if provider_available:

        def fake_apply(*, content, transforms, **_):
            transformed = (
                "<workflow>\n"
                "<critical>SCOPE LIMITATION: read-only validator. Generate validation report.</critical>\n"
                '<step n="1" goal="INVEST">'
                "<action>Score INVEST criteria and emit a report.</action>"
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

    old = compile_workflow("validate-story", _make_context(dual_project), skill_layout="old")
    new = compile_workflow(
        "bmad-validate-story",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/validate-story",
            tofile="new/bmad-validate-story",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= threshold, (
        f"diff between legacy and new validate-story exceeds {threshold} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
