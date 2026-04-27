"""Side-by-side comparison: legacy vs skill-layout code-review compile.

Mirror of :mod:`tests.skill_layout.test_dev_story_compat`. Stands up
a single project that satisfies *both* compiler paths (it has the
v6.4+ skill at ``.claude/skills/...`` AND a legacy
``_bmad/bmm/workflows/4-implementation/code-review/`` directory),
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
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-code-review"
LEGACY_WORKFLOW = REPO_ROOT / "src" / "bmad_assist" / "workflows" / "code-review"


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
        "development_status:\n  10-1-initial-setup: review\n"
    )
    (sprint / "10-1-initial-setup.md").write_text(
        "# Story 10.1: Initial Setup\n\n"
        "## Status\n\nreview\n\n"
        "## Acceptance Criteria\n\n- [x] AC1\n\n"
        "## Tasks/Subtasks\n\n- [x] Task 1\n\n"
        "## Dev Notes\n\nImplementation complete.\n\n"
        "## File List\n\n- src/foo.py\n"
    )
    return docs


def _install_new_layout(project_root: Path) -> None:
    """Mirror the bundled skill into the project's ``.claude/skills/``."""
    target = project_root / ".claude" / "skills" / "bmad-code-review"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# marker\n")


def _install_old_layout(project_root: Path) -> Path:
    """Copy the bundled legacy workflow into the project tree."""
    target = project_root / "_bmad" / "bmm" / "workflows" / "4-implementation" / "code-review"
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
    """Bounded-difference invariants between legacy and skill-layout code-review."""

    def test_both_paths_produce_compiled_workflows(self, dual_project: Path) -> None:
        """Both compile paths return non-empty :class:`CompiledWorkflow`s."""
        old = compile_workflow("code-review", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-code-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        assert isinstance(old, CompiledWorkflow)
        assert isinstance(new, CompiledWorkflow)
        assert old.context, "legacy compile produced empty body"
        assert new.context, "new compile produced empty body"

    def test_compiled_workflow_envelope_present_on_both_sides(self, dual_project: Path) -> None:
        """Both bodies retain the ``<compiled-workflow>`` outer envelope.

        Note: bmad-code-review is a v6.4+ step-file-architecture skill,
        so unlike dev-story / create-story the new-side SKILL.md does
        NOT have an inline ``<workflow>...</workflow>`` block (each
        step file is loaded just-in-time at runtime). The legacy
        instructions.xml does include ``<workflow>``. We assert on the
        compiler's outer ``<compiled-workflow>`` envelope, which both
        paths produce.
        """
        old = compile_workflow("code-review", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-code-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "<compiled-workflow>" in body, (
                f"{label} body missing <compiled-workflow>"
            )

    def test_both_embed_project_context(self, dual_project: Path) -> None:
        """Both paths pull the project-context file into the prompt."""
        old = compile_workflow("code-review", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-code-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "project_context.md" in body or "project-context" in body, (
                f"{label} body should reference project context"
            )

    def test_both_embed_story_file(self, dual_project: Path) -> None:
        """Both paths embed the story file in the context section."""
        old = compile_workflow("code-review", _make_context(dual_project), skill_layout="old")
        new = compile_workflow(
            "bmad-code-review",
            _make_context(dual_project),
            skill_layout="new",
        )
        for body, label in [(old.context, "legacy"), (new.context, "new")]:
            assert "10-1-initial-setup" in body, f"{label} body should reference the story by key"


# --------------------------------------------------------------------------- #
# Bounded-diff under provider availability                                    #
# --------------------------------------------------------------------------- #


# Diff thresholds — see create_story_compat for the rationale. We use
# the create-story / dev-story baseline (800/700) as Phase 3.1 set per
# the 3.2 plan. If empirical observations exceed these caps, raise the
# cap WITH RATIONALE rather than tightening to mask regressions.
#
# code-review's SKILL.md is much shorter (~90 lines + 4 step files
# loaded just-in-time at runtime) than create-story / dev-story, so
# the new-side body is materially smaller than the legacy XML
# instructions. The diff is dominated by the legacy compiler's
# verbose `<context>` payload (TEA artifacts, antipatterns, source
# files, git diff section) which the new path also produces. Empirical
# numbers stay well below 800/700.
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
    """Build a minimal Config-like stub.

    code-review is a MULTI_LLM_PHASES phase, so the base class consults
    ``config.providers.multi`` via :func:`get_phase_provider_config`
    before falling through to ``config.providers.master``. We populate
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


@pytest.mark.parametrize("provider_available", [True, False])
def test_legacy_vs_new_compile_diff_is_bounded(
    provider_available: bool,
    dual_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded-diff invariant between legacy and skill-layout code-review."""
    import difflib

    from bmad_assist.compiler.patching.types import TransformResult
    from bmad_assist.compiler.skills import bmad_code_review as skill_mod

    if provider_available:

        def fake_apply(*, content, transforms, **_):
            # Mirror the structure the patch's must_contain rules expect:
            #   * <step ... must survive renumbering (use n="3" / n="4");
            #   * <critical preserved;
            #   * "report" / "adversarial" tokens present;
            #   * <o> block in step n="4" so post_process can renumber
            #     it to n="3" and inject CODE_REVIEW_REPORT_START / _END
            #     markers — both required by must_contain.
            transformed = (
                "<workflow>\n"
                "<critical>SCOPE LIMITATION: read-only adversarial review.</critical>\n"
                '<step n="3" goal="Review">'
                "<action>Run the adversarial review attack plan.</action>"
                "</step>\n"
                '<step n="4" goal="Present findings">'
                "<o>Code review report goes here.</o>"
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

    old = compile_workflow("code-review", _make_context(dual_project), skill_layout="old")
    new = compile_workflow(
        "bmad-code-review",
        _make_context(dual_project),
        skill_layout="new",
    )

    diff = list(
        difflib.unified_diff(
            old.context.splitlines(),
            new.context.splitlines(),
            fromfile="legacy/code-review",
            tofile="new/bmad-code-review",
            lineterm="",
            n=0,
        )
    )
    change_lines = [
        line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    assert len(change_lines) <= threshold, (
        f"diff between legacy and new code-review exceeds {threshold} lines "
        f"(provider_available={provider_available}, observed={len(change_lines)}). "
        "Either the new compiler regressed or the threshold needs adjustment."
    )
