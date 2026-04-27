"""Tests for the layout-aware init bootstrap (Phase 4 — Component C).

Covers the three branches in :func:`bmad_assist.core.project_setup.ensure_project_setup`:

* Existing v6.4+ install → no-clobber.
* Existing legacy ``_bmad/bmm/workflows`` install → legacy copy.
* Fresh project → bootstrap new layout under
  ``.claude/skills/<id>/`` and ``.agents/skills/<id>/``.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from bmad_assist.core.project_setup import (
    bootstrap_new_layout,
    ensure_project_setup,
)
from bmad_assist.skill_layout import detect_layout


def _quiet() -> Console:
    return Console(quiet=True)


# --- bootstrap_new_layout ----------------------------------------------------


def test_bootstrap_creates_skill_md_in_both_mirrors(tmp_path: Path) -> None:
    """Bootstrap copies SKILL.md into both .claude and .agents mirrors."""
    bootstrapped, _skipped = bootstrap_new_layout(tmp_path, force=False, console=_quiet())
    assert "bmad-create-story" in bootstrapped

    claude_md = tmp_path / ".claude" / "skills" / "bmad-create-story" / "SKILL.md"
    agents_md = tmp_path / ".agents" / "skills" / "bmad-create-story" / "SKILL.md"
    assert claude_md.is_file()
    assert agents_md.is_file()
    # Mirrors should be byte-identical.
    assert claude_md.read_bytes() == agents_md.read_bytes()


def test_bootstrap_no_clobber_by_default(tmp_path: Path) -> None:
    """Existing skill directories are skipped without --force."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    sentinel = skill_dir / "SKILL.md"
    sentinel.write_text("USER OWNED\n", encoding="utf-8")

    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    assert sentinel.read_text(encoding="utf-8") == "USER OWNED\n"


def test_bootstrap_force_overwrites(tmp_path: Path) -> None:
    """``force=True`` re-bootstraps over existing skill directories."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("USER OWNED\n", encoding="utf-8")

    bootstrap_new_layout(tmp_path, force=True, console=_quiet())

    # Sentinel was overwritten by the bundled SKILL.md.
    assert "USER OWNED" not in (skill_dir / "SKILL.md").read_text(encoding="utf-8")


def test_bootstrap_copies_supporting_files(tmp_path: Path) -> None:
    """customize.toml and other resources are included in the copy."""
    bootstrap_new_layout(tmp_path, force=False, console=_quiet())
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    # customize.toml is part of every bundled skill.
    assert (skill_dir / "customize.toml").is_file()


# --- ensure_project_setup branches ------------------------------------------


def test_fresh_project_auto_bootstraps_new_layout(tmp_path: Path) -> None:
    """A fresh project + auto detection bootstraps the new layout."""
    result = ensure_project_setup(tmp_path, console=_quiet(), skill_layout="auto")

    assert result.layout == "new"
    assert result.skills_bootstrapped, "expected bundled skills to be bootstrapped"
    # No legacy workflow copy should have happened.
    assert not result.workflows_copied
    assert not result.config_created
    # Detection should now classify the project as new layout.
    assert detect_layout(tmp_path) == "new"


def test_init_skill_layout_new_bootstraps_on_fresh(tmp_path: Path) -> None:
    """Forcing --skill-layout=new on a fresh project bootstraps."""
    result = ensure_project_setup(
        tmp_path, console=_quiet(), skill_layout="new"
    )
    assert result.layout == "new"
    assert (tmp_path / ".claude" / "skills" / "bmad-create-story" / "SKILL.md").is_file()


def test_existing_v64_install_is_not_clobbered(tmp_path: Path) -> None:
    """A pre-existing .claude/skills/bmad-* skill is preserved on auto."""
    # Simulate an existing v6.4+ install.
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    sentinel = skill_dir / "SKILL.md"
    sentinel.write_text("EXISTING USER SKILL\n", encoding="utf-8")
    (skill_dir / "customize.toml").write_text("", encoding="utf-8")

    result = ensure_project_setup(tmp_path, console=_quiet(), skill_layout="auto")

    assert result.layout == "new"
    # No clobber — sentinel still present.
    assert sentinel.read_text(encoding="utf-8") == "EXISTING USER SKILL\n"
    # Should not have done the legacy copy.
    assert not result.workflows_copied
    # And should not have force-bootstrapped without --force.
    assert not result.skills_bootstrapped


def test_existing_legacy_install_keeps_legacy_path(tmp_path: Path) -> None:
    """A project with legacy _bmad/bmm/workflows runs the legacy code path."""
    # Simulate an existing legacy install.
    legacy = tmp_path / "_bmad" / "bmm" / "workflows" / "4-implementation" / "create-story"
    legacy.mkdir(parents=True)
    (legacy / "workflow.yaml").write_text("name: create-story\n", encoding="utf-8")

    result = ensure_project_setup(tmp_path, console=_quiet(), skill_layout="auto")

    assert result.layout == "old"
    # Legacy config should be created.
    assert (tmp_path / "_bmad" / "bmm" / "config.yaml").is_file()
    # No new-layout bootstrap.
    assert not result.skills_bootstrapped
    assert not (tmp_path / ".claude" / "skills" / "bmad-create-story").exists()


def test_skill_layout_old_forces_legacy(tmp_path: Path) -> None:
    """Explicit --skill-layout=old uses the legacy copy path."""
    result = ensure_project_setup(tmp_path, console=_quiet(), skill_layout="old")
    assert result.layout == "old"
    assert (tmp_path / "_bmad" / "bmm" / "config.yaml").is_file()
    assert not (tmp_path / ".claude" / "skills" / "bmad-create-story").exists()


def test_bootstrap_then_discovery_resolves_skill(tmp_path: Path) -> None:
    """End-to-end: bootstrap a fresh project, then discover_workflow_source hits 'new'."""
    from bmad_assist.compiler.workflow_discovery import discover_workflow_source

    ensure_project_setup(tmp_path, console=_quiet(), skill_layout="new")
    # Detection should now classify as new.
    assert detect_layout(tmp_path) == "new"

    source = discover_workflow_source("create-story", tmp_path)
    assert source is not None
    assert source.layout == "new"
    assert source.skill_id == "bmad-create-story"
    assert source.path == tmp_path / ".claude" / "skills" / "bmad-create-story"


def test_force_on_existing_v64_re_bootstraps(tmp_path: Path) -> None:
    """``reset_workflows`` (force=True) on existing v6.4+ re-bootstraps."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("OLD\n", encoding="utf-8")
    (skill_dir / "customize.toml").write_text("", encoding="utf-8")

    result = ensure_project_setup(
        tmp_path, console=_quiet(), skill_layout="auto", force=True
    )

    assert result.layout == "new"
    assert result.skills_bootstrapped, "force=True should re-bootstrap"
    # Verify content was overwritten.
    assert "OLD" not in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
