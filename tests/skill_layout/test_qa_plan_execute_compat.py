"""Side-by-side: legacy vs skill-layout qa-plan-execute compile.

Like qa-plan-generate, this orphan's legacy source is Markdown rather
than XML. The compat test confirms bounded-difference invariants hold
across the source-extension change.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-qa-plan-execute"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "qa-plan-execute"


def _seed_artifacts(project_root: Path) -> Path:
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


def _install_new_layout(project_root: Path) -> None:
    target = project_root / ".claude" / "skills" / "bmad-qa-plan-execute"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    target = project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / "qa-plan-execute"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(LEGACY_WORKFLOW, target)
    config_dir = project_root / "_bmad" / "bmm"
    config = config_dir / "config.yaml"
    output_folder = project_root / "out"
    config.write_text(
        f"project_name: test\n"
        f"output_folder: '{output_folder}'\n"
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
    output_folder = project_root / "out"
    return CompilerContext(
        project_root=project_root,
        output_folder=output_folder,
        project_knowledge=output_folder,
        resolved_variables={"epic_num": 10},
    )


# --------------------------------------------------------------------------- #
# Side-by-side                                                                #
# --------------------------------------------------------------------------- #


class TestSideBySideCompatibility:
    """Tests for SideBySideCompatibility."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Test both paths produce compiled workflows."""
        old = compile_workflow("qa-plan-execute", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context
        assert new.context

    def test_both_reference_test_plan(self, dual_project: Path) -> None:
        """Test both reference test plan."""
        old = compile_workflow("qa-plan-execute", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "test plan" in body.lower(), f"{label} body should mention test plan"

    def test_both_carry_non_interactive_signal(self, dual_project: Path) -> None:
        """Both paths preserve the non-interactive / auto-continue rule."""
        old = compile_workflow("qa-plan-execute", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "non-interactive" in body.lower() or "non_interactive" in body.lower(), (
                f"{label} body should signal non-interactive headless execution"
            )

    def test_new_path_is_workflow_xml_well_formed(self, dual_project: Path) -> None:
        """Test new path is workflow xml well formed."""
        new = compile_workflow(
            "bmad-qa-plan-execute",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert "<workflow>" in new.context
        assert "</workflow>" in new.context


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# qa-plan-execute legacy source is ~22k Markdown; new SKILL.md is
# concise XML-wrapped outcome-based markdown. Diff is large by
# construction (different format AND outcome-based rewrite).
_DIFF_THRESHOLD = 2200


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

    from bmad_assist.compiler.patching.types import TransformResult
    from bmad_assist.compiler.skills import bmad_qa_plan_execute as skill_mod

    if provider_available:

        def fake_apply(*, content, transforms, **_):
            transformed = (
                "<workflow>\n"
                "<critical>NON-INTERACTIVE MODE.</critical>\n"
                '<step n="1" goal="Init">'
                "<action>## Step 1 Init. Parse plan and execute.</action>"
                "</step>\n"
                '<step n="4" goal="Cat A">'
                "<action>## Step 4: Execute Category A.</action>"
                "</step>\n"
                '<step n="6" goal="Results">'
                "<action>## Step 6: Generate Results.</action>"
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
    else:
        monkeypatch.setattr(
            "bmad_assist.core.config.get_config",
            lambda: _stub_config_with_master(None),
        )

    old = compile_workflow("qa-plan-execute", _make_context(dual_project), skill_layout="old")
    new = compile_workflow(
        "bmad-qa-plan-execute",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/qa-plan-execute",
            tofile="new/bmad-qa-plan-execute",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= _DIFF_THRESHOLD, (
        f"diff between legacy and new qa-plan-execute exceeds {_DIFF_THRESHOLD} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
