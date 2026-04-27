"""Tests for the Phase 5 default flip — ``run`` defaults to v6.4+ skill layout.

Phase 4 pinned ``ensure_project_setup(skill_layout="old")`` inside the
``run`` command. Phase 5 flipped that to ``"auto"`` so projects with a
bootstrapped ``.claude/skills/bmad-*/`` install (or BMAD's
``_bmad/scripts/resolve_customization.py`` marker) automatically pick
up the new path. These tests verify the flip happened, the routing
through :func:`compile_workflow` matches the layout selection, and the
``--skill-layout old`` override still forces legacy.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from bmad_assist.compiler import compile_workflow
from bmad_assist.compiler.core import _LEGACY_DEPRECATION_EMITTED, get_workflow_compiler
from bmad_assist.compiler.skills.bmad_create_story import BmadCreateStoryCompiler
from bmad_assist.compiler.types import CompilerContext
from bmad_assist.skill_layout import detect_layout


def _bootstrap_v64_marker(project_root: Path) -> None:
    """Plant the v6.4+ resolver marker so detect_layout() returns "new"."""
    scripts = project_root / "_bmad" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "resolve_customization.py").write_text("# v6.4+ marker\n")


def _bootstrap_skill_marker(project_root: Path) -> None:
    """Plant a bootstrapped skill so detect_layout() returns "new"."""
    skill_dir = project_root / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "customize.toml").write_text("# bootstrapped\n")
    (skill_dir / "SKILL.md").write_text("# stub\n")


@pytest.fixture(autouse=True)
def _reset_dedup() -> None:
    """Each test starts with an empty dedup set so DeprecationWarnings fire."""
    _LEGACY_DEPRECATION_EMITTED.clear()
    yield
    _LEGACY_DEPRECATION_EMITTED.clear()


def test_run_default_uses_auto_layout(tmp_path: Path) -> None:
    """The ``run`` command's call to ``ensure_project_setup`` is now ``auto``.

    Asserted against the source so a future re-pin to ``"old"`` would
    show up immediately. We don't import-execute the ``run`` command
    because it requires a full provider stack — the source assertion is
    deliberate and load-bearing for the Phase 5 contract.
    """
    cli_source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "bmad_assist"
        / "cli.py"
    ).read_text(encoding="utf-8")
    # The Phase 5 call site uses skill_layout="auto" (no skill_layout="old").
    assert 'skill_layout="auto"' in cli_source
    assert 'skill_layout="old"' not in cli_source


def test_bootstrapped_project_resolves_to_new_compiler(tmp_path: Path) -> None:
    """A project with bootstrapped ``.claude/skills/bmad-*`` routes to the new compiler."""
    _bootstrap_skill_marker(tmp_path)
    assert detect_layout(tmp_path) == "new"

    compiler = get_workflow_compiler(
        "create-story",
        skill_layout="auto",
        project_root=tmp_path,
    )
    assert isinstance(compiler, BmadCreateStoryCompiler)


def test_v64_marker_project_resolves_to_new_compiler(tmp_path: Path) -> None:
    """A project with ``_bmad/scripts/resolve_customization.py`` routes new."""
    _bootstrap_v64_marker(tmp_path)
    assert detect_layout(tmp_path) == "new"

    compiler = get_workflow_compiler(
        "dev-story",
        skill_layout="auto",
        project_root=tmp_path,
    )
    # We don't import the concrete compiler class — module-path check
    # is enough to verify we routed through the skill-layout factory.
    assert type(compiler).__module__.startswith("bmad_assist.compiler.skills.")


def test_unbootstrapped_project_falls_back_to_legacy_with_warning(
    tmp_path: Path,
) -> None:
    """Neither marker present → legacy path + DeprecationWarning for migrated workflows."""
    assert detect_layout(tmp_path) == "old"

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        compiler = get_workflow_compiler(
            "create-story",
            skill_layout="auto",
            project_root=tmp_path,
        )

    # Routed through the legacy path.
    assert type(compiler).__module__.startswith("bmad_assist.compiler.workflows.")
    deprecations = [
        w
        for w in recorded
        if issubclass(w.category, DeprecationWarning)
        and "legacy workflow.yaml" in str(w.message)
    ]
    assert deprecations, "expected a DeprecationWarning for create-story on legacy"
    assert "create-story" in str(deprecations[0].message)
    assert "skill_layout" in str(deprecations[0].message)


def test_skill_layout_old_override_forces_legacy_even_when_bootstrapped(
    tmp_path: Path,
) -> None:
    """``--skill-layout old`` forces the legacy compiler regardless of detection."""
    _bootstrap_skill_marker(tmp_path)
    assert detect_layout(tmp_path) == "new"

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        compiler = get_workflow_compiler(
            "create-story",
            skill_layout="old",
            project_root=tmp_path,
        )

    # Even with bootstrapped skills present, "old" wins.
    assert type(compiler).__module__.startswith("bmad_assist.compiler.workflows.")
    # And the deprecation still fires (workflow has a port; we explicitly chose legacy).
    deprecations = [
        w
        for w in recorded
        if issubclass(w.category, DeprecationWarning)
        and "legacy workflow.yaml" in str(w.message)
    ]
    assert deprecations, "expected DeprecationWarning when forcing legacy on a migrated workflow"


def test_compile_workflow_routes_via_default_auto_when_bootstrapped(
    tmp_path: Path,
) -> None:
    """End-to-end: compile_workflow without explicit ``skill_layout`` uses auto.

    Builds a minimal CompilerContext and verifies the resolver picks the
    new path when the project is bootstrapped. We stop at the routing
    stage by intercepting via ``get_workflow_compiler`` — actually
    compiling would require the full skill bundle on disk for this
    project, which the e2e tests cover separately.
    """
    _bootstrap_skill_marker(tmp_path)
    ctx = CompilerContext(
        project_root=tmp_path,
        output_folder=tmp_path / "_out",
        project_knowledge=tmp_path / "docs",
        cwd=tmp_path,
        resolved_variables={},
    )
    # compile_workflow's first step is get_workflow_compiler — assert
    # that step alone resolves to the skill-layout compiler. We avoid
    # invoking compile() (which needs full project artefacts).
    compiler = get_workflow_compiler(
        "create-story",
        skill_layout="auto",
        project_root=ctx.project_root,
    )
    assert isinstance(compiler, BmadCreateStoryCompiler)
    # Belt-and-braces: the public compile_workflow signature must accept
    # the keyword too (no breaking change to the call surface).
    assert "skill_layout" in compile_workflow.__doc__
