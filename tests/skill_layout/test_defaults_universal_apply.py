"""End-to-end proof that bundled defaults apply universally.

This test guards the architectural fix in
:meth:`SkillLayoutCompilerBase._apply_patch_post_process` and
:func:`bmad_assist.compiler.shared_utils.apply_post_process`: the
post-process pipeline always runs the bundled
``default_patches/defaults.yaml`` (and ``defaults-testarch.yaml`` for
TEA workflows), even when no per-workflow ``.patch.yaml`` is present
in the consumer project.

The trace evidence that motivated the fix: when bmad-assist ran ATDD
from the algo project, the compiled prompt still contained
"confirm with the user" prose at line 1091 because the per-workflow
patch wasn't shipped to the consumer project, and the old
``_apply_patch_post_process`` short-circuited when ``patch_path`` was
``None`` — so the bundled defaults never fired. Without the universal-
defaults code path this regression would silently re-appear; this test
catches it by compiling ``bmad-testarch-atdd`` from a fresh tmp_path
consumer-project setup with NO ``.bmad-assist/patches/`` directory and
asserting the gate-strip rule still landed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.types import CompilerContext

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILL = REPO_ROOT / "src" / "bmad_assist" / "skills" / "bmad-testarch-atdd"


def _install_skill(project_root: Path) -> None:
    """Mirror the bundled skill into ``.claude/skills/bmad-testarch-atdd/``.

    Crucially: does NOT install ``.bmad-assist/patches/`` — that is the
    point of this test. A consumer project without dev-time patches
    must still see the framework defaults applied.
    """
    target = project_root / ".claude" / "skills" / "bmad-testarch-atdd"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_SKILL, target)
    # v6.4+ marker so detect_layout() returns "new".
    scripts_dir = project_root / "_bmad" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "resolve_customization.py").write_text("# test marker\n")


def _seed_project_artifacts(project_root: Path) -> None:
    """Populate the tmp project with the docs the testarch-atdd compiler expects."""
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "project_context.md").write_text(
        "# Project Context\n\nMinimal context for headless-defaults test.\n"
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


@pytest.fixture
def consumer_project_without_patches(tmp_path: Path) -> Path:
    """A v6.4+ consumer project with the skill installed but NO patches.

    This mirrors the state of pip-installed downstream projects: the
    ``default_patches/`` defaults ship via the package, but the
    dev-time per-workflow ``.bmad-assist/patches/*.patch.yaml`` files
    do NOT ship. Confirming the absence is part of the contract.
    """
    proj = tmp_path / "consumer"
    proj.mkdir()
    _seed_project_artifacts(proj)
    _install_skill(proj)
    # Sanity guard the test's own premise.
    assert not (proj / ".bmad-assist" / "patches").exists()
    return proj


def test_tea_defaults_apply_when_no_per_workflow_patch_present(
    consumer_project_without_patches: Path,
) -> None:
    """Bundled defaults must apply when no per-workflow patch is present.

    Compiling bmad-testarch-atdd in a project with no per-workflow
    patch must still apply the bundled ``defaults-testarch.yaml`` —
    specifically the headless-confirmation-gate rule that strips the
    "confirm with the user" prose from the compiled step chain.
    """
    project = consumer_project_without_patches
    docs = project / "docs"
    context = CompilerContext(
        project_root=project,
        output_folder=docs,
        project_knowledge=docs,
        resolved_variables={"epic_num": 10, "story_num": 1},
    )

    result = compile_workflow(
        "bmad-testarch-atdd",
        context,
        skill_layout="new",
    )
    body = result.context

    # Sanity: compile actually produced TEA content.
    assert body, "compiled workflow body must be non-empty"
    assert "<compiled-workflow>" in body
    # The step chain MUST be embedded (otherwise the gate prose isn't
    # in scope for stripping in the first place).
    assert "Confirm Inputs" in body, (
        "step-01 prose must be embedded — otherwise this test is vacuous"
    )

    # Universal-defaults proof: the gate-strip rule from
    # default_patches/defaults-testarch.yaml ran.
    assert "confirm with the user" not in body, (
        "Bundled defaults-testarch.yaml gate-strip rule did not fire — "
        "this means the defaults pipeline is short-circuited again when "
        "no per-workflow patch is on disk. See "
        "SkillLayoutCompilerBase._apply_patch_post_process and "
        "shared_utils.apply_post_process — both must invoke load_defaults "
        "regardless of patch_path."
    )
    assert "headless mode" in body, (
        "Gate-strip rule's headless-mode replacement marker is missing — "
        "the rule may have matched but the replacement string changed."
    )
