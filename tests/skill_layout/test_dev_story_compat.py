"""Side-by-side comparison: legacy vs skill-layout dev-story compile.

Mirror of :mod:`tests.skill_layout.test_create_story_compat`. Stands
up a single project that satisfies *both* compiler paths (it has the
v6.4+ skill at ``.claude/skills/...`` AND a legacy
``_bmad/bmm/workflows/4-implementation/dev-story/`` directory),
compiles through each, and asserts a bounded set of structural
invariants.

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
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-dev-story"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "dev-story"


def _seed_artifacts(project_root: Path) -> Path:
    """Populate the project tree with the docs both compilers expect."""
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
        "development_status:\n  10-1-initial-setup: ready-for-dev\n"
    )
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\nready-for-dev\n\n"
        "## Acceptance Criteria\n\n- [ ] AC1\n\n"
        "## Tasks/Subtasks\n\n- [ ] Task 1\n\n"
        "## Dev Notes\n\nMinimal notes.\n"
    )
    return docs


def _install_new_layout(project_root: Path) -> None:
    """Mirror the bundled skill into the project's ``.claude/skills/``."""
    target = project_root / ".claude" / "skills" / "bmad-dev-story"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    """Copy the bundled legacy workflow into the project tree."""
    target = project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / "dev-story"
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
        resolved_variables={"epic_num": 10, "story_num": 1},
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
    """Bounded-difference invariants between legacy and skill-layout dev-story."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Both compile paths return non-empty :class:`CompiledWorkflow`s."""
        old = compile_workflow("dev-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-dev-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context, "legacy compile produced empty body"
        assert new.context, "new compile produced empty body"

    def test_workflow_xml_structure_present_on_both_sides(self, dual_project: Path) -> None:
        """Both bodies retain the ``<workflow>...</workflow>`` envelope."""
        old = compile_workflow("dev-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-dev-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<workflow>" in body, f"{label} body missing <workflow>"
            assert "</workflow>" in body, f"{label} body missing </workflow>"

    def test_both_have_overlapping_step_set(self, dual_project: Path) -> None:
        """Step-number sets share at least one element across paths."""
        old = compile_workflow("dev-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-dev-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        old_steps = _normalize_steps(old.context)
        new_steps = _normalize_steps(new.context)
        assert old_steps, "legacy compile produced no <step n='N'> markers"
        assert new_steps, "new compile produced no <step n='N'> markers"
        assert set(old_steps) & set(new_steps), (
            f"step sets are disjoint: legacy={old_steps}, new={new_steps}"
        )

    def test_both_embed_project_context(self, dual_project: Path) -> None:
        """Both paths pull the project-context file into the prompt."""
        old = compile_workflow("dev-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-dev-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "project_context.md" in body or "project-context" in body, (
                f"{label} body should reference project context"
            )

    def test_both_embed_story_file(self, dual_project: Path) -> None:
        """Both paths embed the story file in the context section."""
        old = compile_workflow("dev-story", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-dev-story",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "10-1-initial-setup" in body, f"{label} body should reference the story by key"


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# Diff thresholds — see create_story_compat for the rationale. Empirical
# numbers on the seeded fixture:
#
#   * provider_available=True  → mocked LLM body diffs at ~330–360 lines
#     against the legacy regex-only output. We cap at 800 to match
#     create-story's create-side cap and leave headroom for harmless
#     prose tweaks while flagging an order-of-magnitude expansion.
#   * provider_available=False → both paths run regex-only; observed
#     diff is ~430–500 lines (the new SKILL.md is verbose markdown vs
#     the legacy compact XML). Cap at 700 to mirror the create-story
#     compat threshold.
#
# Dev-story's caps end up identical to create-story's — the dev-story
# patch is more involved, but the legacy/new structural drift is on
# the same order of magnitude. If Phase 3.2 finds a workflow whose
# diff exceeds these caps, raise the cap **with rationale** rather
# than masking the regression.
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
    """Bounded-diff invariant between legacy and skill-layout dev-story."""
    import difflib

    from bmad_assist.compiler.patching.types import TransformResult
    from bmad_assist.compiler.skills import bmad_dev_story as skill_mod

    if provider_available:

        def fake_apply(*, content, transforms, **_):
            # Include the dev-story invariants — "red-green-refactor",
            # "implement", "test" — required by the patch's
            # must_contain block. Use a survivor step number (3,5,6,7,8)
            # so post_process renumber leaves a <step in place.
            transformed = (
                "<workflow>\n"
                "<critical>SCOPE: implement the story per the spec.</critical>\n"
                "<critical>Git Intelligence is EMBEDDED above; do not run git.</critical>\n"
                '<step n="6" goal="Implement">'
                "<action>Follow red-green-refactor cycle: write a failing test, "
                "implement minimal code, then refactor.</action></step>\n"
                '<step n="8" goal="Validate">'
                "<action>Run all tests and verify acceptance criteria.</action>"
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

    old = compile_workflow("dev-story", _make_context(dual_project), skill_layout="old")
    new = compile_workflow(
        "bmad-dev-story",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/dev-story",
            tofile="new/bmad-dev-story",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= threshold, (
        f"diff between legacy and new dev-story exceeds {threshold} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
