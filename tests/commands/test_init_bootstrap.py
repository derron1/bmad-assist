"""Tests for the init bootstrap.

Covers the two branches in :func:`bmad_assist.core.project_setup.ensure_project_setup`:

* Existing v6.4+ install → no-clobber.
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


def test_fresh_project_bootstraps_new_layout(tmp_path: Path) -> None:
    """A fresh project bootstraps the new layout."""
    result = ensure_project_setup(tmp_path, console=_quiet())

    assert result.layout == "new"
    assert result.skills_bootstrapped, "expected bundled skills to be bootstrapped"
    # No legacy workflow copy should have happened.
    assert not result.workflows_copied
    assert not result.config_created


def test_existing_v64_install_is_not_clobbered(tmp_path: Path) -> None:
    """A pre-existing .claude/skills/bmad-* skill is preserved."""
    # Simulate an existing v6.4+ install in BOTH mirrors so the
    # bootstrap has nothing to do for create-story.
    for mirror in (".claude/skills", ".agents/skills"):
        skill_dir = tmp_path / mirror / "bmad-create-story"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("EXISTING USER SKILL\n", encoding="utf-8")
        (skill_dir / "customize.toml").write_text("", encoding="utf-8")

    result = ensure_project_setup(tmp_path, console=_quiet())

    assert result.layout == "new"
    # No clobber — sentinel still present in both mirrors.
    for mirror in (".claude/skills", ".agents/skills"):
        sentinel = tmp_path / mirror / "bmad-create-story" / "SKILL.md"
        assert sentinel.read_text(encoding="utf-8") == "EXISTING USER SKILL\n"
    # Should not have done the legacy copy.
    assert not result.workflows_copied
    # The pre-existing skill is reported as skipped, not bootstrapped.
    assert "bmad-create-story" in result.skills_skipped
    assert "bmad-create-story" not in result.skills_bootstrapped


def test_existing_legacy_install_still_bootstraps_new_layout(tmp_path: Path) -> None:
    """A legacy install does not suppress the new-layout bootstrap."""
    # Simulate an existing legacy install.
    legacy = tmp_path / "_bmad" / "bmm" / "workflows" / "4-implementation" / "create-story"
    legacy.mkdir(parents=True)
    (legacy / "workflow.yaml").write_text("name: create-story\n", encoding="utf-8")

    result = ensure_project_setup(tmp_path, console=_quiet())

    assert result.layout == "new"
    # New-layout bootstrap proceeded even though _bmad/... is present.
    assert result.skills_bootstrapped
    assert (tmp_path / ".claude" / "skills" / "bmad-create-story" / "SKILL.md").is_file()


def test_bootstrap_then_discovery_resolves_skill(tmp_path: Path) -> None:
    """End-to-end: bootstrap a fresh project, then discover_workflow_source hits 'new'."""
    from bmad_assist.compiler.workflow_discovery import discover_workflow_source

    ensure_project_setup(tmp_path, console=_quiet())

    source = discover_workflow_source("bmad-create-story", tmp_path)
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

    result = ensure_project_setup(tmp_path, console=_quiet(), force=True)

    assert result.layout == "new"
    assert result.skills_bootstrapped, "force=True should re-bootstrap"
    # Verify content was overwritten.
    assert "OLD" not in (skill_dir / "SKILL.md").read_text(encoding="utf-8")


def test_legacy_skill_layout_kwarg_swallowed(tmp_path: Path) -> None:
    """Pre-0.6.0 callers passing ``skill_layout=`` keyword are tolerated."""
    result = ensure_project_setup(tmp_path, console=_quiet(), skill_layout="auto")
    assert result.layout == "new"
    assert (tmp_path / ".claude" / "skills" / "bmad-create-story" / "SKILL.md").is_file()
