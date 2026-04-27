"""Side-by-side comparison: legacy vs skill-layout retrospective compile.

Mirror of :mod:`tests.skill_layout.test_dev_story_compat`. Stands up
a single project that satisfies *both* compiler paths (it has the
v6.4+ skill at ``.claude/skills/...`` AND a legacy
``_bmad/bmm/workflows/4-implementation/retrospective/`` directory),
compiles through each, and asserts a bounded set of structural
invariants.

The retrospective workflow operates over an entire epic and does not
require a story file in its validate_context — but the legacy
compiler still reads story files as context, so the seeded fixture
includes one for each test.

The test does **not** require byte-for-byte identity: the substituted
body, source-comment headers, and customize-toml-driven blocks differ
by design.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompiledWorkflow, CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-retrospective"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "retrospective"


def _seed_artifacts(project_root: Path) -> Path:
    """Populate the project tree with the docs both compilers expect."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text("# Project Context\n\nMinimal context for tests.\n")
    (docs / "prd.md").write_text("# PRD\n\nProject requirements.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nLayered.\n")
    epics = docs / "epics"
    epics.mkdir()
    (epics / "epic-10.md").write_text(
        "# Epic 10: Test Epic\n\n## Story 10.1: Initial Setup\n\nContent.\n"
    )
    sprint = docs / "sprint-artifacts"
    sprint.mkdir()
    (sprint / "sprint-status.yaml").write_text(
        "development_status:\n"
        "  10-1-initial-setup: done\n"
        "  epic-10: complete\n"
        "  epic-10-retrospective: pending\n"
    )
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\ndone\n\n"
        "## Acceptance Criteria\n\n- [x] AC1\n\n"
        "## Tasks/Subtasks\n\n- [x] Task 1\n\n"
        "## Dev Notes\n\nImplementation complete.\n"
    )
    return docs


def _install_new_layout(project_root: Path) -> None:
    """Mirror the bundled skill into the project's ``.claude/skills/``."""
    target = project_root / ".claude" / "skills" / "bmad-retrospective"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    """Copy the bundled legacy workflow into the project tree."""
    target = project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / "retrospective"
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
    """Build the compiler context shared across both compile paths.

    ``output_folder`` is ``docs/`` — the legacy compiler resolves the
    stories dir as ``output_folder/sprint-artifacts/``.
    """
    docs = project_root / "docs"
    return CompilerContext(
        project_root=project_root,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10},
    )


def _normalize_steps(text: str) -> list[str]:
    """Return the set of ``<step n="N" goal="...">`` markers, normalised."""
    import re

    return sorted(set(re.findall(r"<step\s+n=\"?(\d+)\"?[^>]*>", text)))


def _has_marker(text: str, markers: Iterable[str]) -> dict[str, bool]:
    """Return a presence map for each marker against ``text``."""
    return {m: m in text for m in markers}


# --------------------------------------------------------------------------- #
# Side-by-side                                                                #
# --------------------------------------------------------------------------- #


class TestSideBySideCompatibility:
    """Bounded-difference invariants between legacy and skill-layout retrospective."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Both compile paths return non-empty :class:`CompiledWorkflow`s."""
        old = compile_workflow("retrospective", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-retrospective",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context, "legacy compile produced empty body"
        assert new.context, "new compile produced empty body"

    def test_workflow_xml_structure_present_on_both_sides(self, dual_project: Path) -> None:
        """Both bodies retain the ``<workflow>...</workflow>`` envelope."""
        old = compile_workflow("retrospective", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-retrospective",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<workflow>" in body, f"{label} body missing <workflow>"
            assert "</workflow>" in body, f"{label} body missing </workflow>"

    def test_both_embed_project_context(self, dual_project: Path) -> None:
        """Both paths pull the project-context file into the prompt."""
        old = compile_workflow("retrospective", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-retrospective",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "project_context.md" in body or "project-context" in body, (
                f"{label} body should reference project context"
            )

    def test_both_reference_epic(self, dual_project: Path) -> None:
        """Both paths reference the target epic in the compiled prompt."""
        old = compile_workflow("retrospective", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-retrospective",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            # Legacy injects the epic via mission/variables; new path
            # leaves runtime tokens like {epic_number} / {{epic_number}}
            # in place since the master agent fills them at runtime.
            # Both should at least mention "epic" somewhere.
            assert "epic" in body.lower(), f"{label} body should mention epic"


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# Diff thresholds — see create_story_compat for the rationale. We use
# the create-story / dev-story baseline (800/700) per the Phase 3.2
# plan. Retrospective's SKILL.md is ~1500 lines (party-mode dialogue
# heavy) versus a much smaller legacy XML, so the new-side body is
# materially larger than the legacy. The patch's post_process
# aggressively strips party-mode dialogue, but unmatched <output>
# blocks survive when names like Bob (legacy) appear instead of the
# Amelia/Alice/Charlie/Dana/Elena set the regex enumerates. If the
# diff later exceeds these caps, raise the threshold WITH RATIONALE
# rather than tightening to mask regressions.
_DIFF_THRESHOLD_PROVIDER_TRUE = 800
_DIFF_THRESHOLD_PROVIDER_FALSE = 700


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
    """Bounded-diff invariant between legacy and skill-layout retrospective."""
    import difflib

    from bmad_assist.compiler.patching.types import TransformResult
    from bmad_assist.compiler.skills import bmad_retrospective as skill_mod

    if provider_available:

        def fake_apply(*, content, transforms, **_):
            # Mirror the structure the patch's must_contain rules expect:
            #   * <step (must_contain),
            #   * step n="11" goal="Save Retrospective Output" so the
            #     marker-injection rule fires and adds the
            #     RETROSPECTIVE_REPORT_START / _END markers (required
            #     by must_contain),
            #   * <workflow> envelope so post_process can prepend the
            #     "AUTOMATED MODE" critical block (also required by
            #     must_contain).
            transformed = (
                "<workflow>\n"
                '<step n="6" goal="Discuss">'
                "<action>Discuss what went well.</action>"
                "</step>\n"
                '<step n="11" goal="Save Retrospective Output">'
                "<action>Output the retrospective report.</action>"
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

    old = compile_workflow("retrospective", _make_context(dual_project), skill_layout="old")
    new = compile_workflow(
        "bmad-retrospective",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/retrospective",
            tofile="new/bmad-retrospective",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= threshold, (
        f"diff between legacy and new retrospective exceeds {threshold} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
