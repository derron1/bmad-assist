"""Tests for the init bootstrap.

Covers the branches in :func:`bmad_assist.core.project_setup.ensure_project_setup`:

* Stamped, current install → no-clobber.
* Stamped, older install → auto-refresh (hash-gates customize.toml).
* Unstamped (legacy) install → auto-refresh.
* Fresh project → bootstrap new layout under
  ``.claude/skills/<id>/`` and ``.agents/skills/<id>/``.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

import bmad_assist
from bmad_assist.core.project_setup import (
    _BUNDLE_VERSION_FILE,
    _hash_file,
    _read_installed_bundle_stamp,
    bootstrap_new_layout,
    ensure_project_setup,
)


def _quiet() -> Console:
    return Console(quiet=True)


def _stamp_skill(skill_dir: Path, version: str) -> None:
    """Write a fake bundle-version stamp into an installed skill dir."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / _BUNDLE_VERSION_FILE).write_text(f"{version}\n", encoding="utf-8")


def _stamp_skill_with_customize_hash(
    skill_dir: Path,
    version: str,
    customize_hash: str,
) -> None:
    """Write a structured fake bundle-version stamp."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / _BUNDLE_VERSION_FILE).write_text(
        (f'{{"customize_toml_hash": "{customize_hash}", "version": "{version}"}}\n'),
        encoding="utf-8",
    )


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


def test_bootstrap_no_clobber_when_stamp_matches(tmp_path: Path) -> None:
    """A current-stamped skill dir is skipped without --force."""
    for mirror in (".claude/skills", ".agents/skills"):
        skill_dir = tmp_path / mirror / "bmad-create-story"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("USER OWNED\n", encoding="utf-8")
        _stamp_skill(skill_dir, bmad_assist.__version__)

    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    for mirror in (".claude/skills", ".agents/skills"):
        sentinel = tmp_path / mirror / "bmad-create-story" / "SKILL.md"
        assert sentinel.read_text(encoding="utf-8") == "USER OWNED\n"


def test_bootstrap_auto_refreshes_unstamped_install(tmp_path: Path) -> None:
    """An unstamped legacy install is auto-refreshed without --force."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("LEGACY STUB\n", encoding="utf-8")
    # No .bundle-version stamp — simulates a pre-stamp install.

    bootstrapped, _ = bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    assert "bmad-create-story" in bootstrapped
    assert "LEGACY STUB" not in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    # Stamp now records the current version.
    stamp = _read_installed_bundle_stamp(skill_dir)
    assert stamp.version == bmad_assist.__version__
    assert stamp.customize_toml_hash


def test_bootstrap_auto_refreshes_stale_stamp(tmp_path: Path) -> None:
    """A stamped install with a stale version is auto-refreshed."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("OLD CONTENT\n", encoding="utf-8")
    _stamp_skill(skill_dir, "0.0.0-stale")

    bootstrapped, _ = bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    assert "bmad-create-story" in bootstrapped
    assert "OLD CONTENT" not in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    stamp = _read_installed_bundle_stamp(skill_dir)
    assert stamp.version == bmad_assist.__version__
    assert stamp.customize_toml_hash


def test_bootstrap_refresh_preserves_customize_toml(tmp_path: Path) -> None:
    """Auto-refresh preserves user customize.toml overrides."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    user_override = "# user override\nkey = 'user-value'\n"
    (skill_dir / "customize.toml").write_text(user_override, encoding="utf-8")
    _stamp_skill(skill_dir, "0.0.0-stale")

    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    assert (skill_dir / "customize.toml").read_text(encoding="utf-8") == user_override


def test_bootstrap_legacy_stamp_does_not_poison_stale_customize(tmp_path: Path) -> None:
    """BOOT-001 regression: legacy one-line stamp must not falsely certify stale content.

    Reproduces the poison vector: an install with a legacy one-line stamp
    (``version == current``, no ``customize_toml_hash``) whose customize.toml is
    a STALE bundled default (differs from the current bundled). The buggy code
    re-stamped with the CURRENT bundled hash without copying — falsely
    certifying stale content as current-bundled, which froze it forever. After
    the fix, the stamp must record the hash of what is actually on disk (the
    stale content), never the current bundled hash.
    """
    from bmad_assist.skills import get_bundled_skill_dir

    src_dir = get_bundled_skill_dir("bmad-create-story")
    assert src_dir is not None
    current_bundled_hash = _hash_file(src_dir / "customize.toml")
    assert current_bundled_hash is not None

    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    stale_customize = "# stale bundled default\nactivation_steps_append = []\n"
    (skill_dir / "customize.toml").write_text(stale_customize, encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("OLD CONTENT\n", encoding="utf-8")
    stale_hash = _hash_file(skill_dir / "customize.toml")
    assert stale_hash != current_bundled_hash
    # Legacy one-line stamp at the CURRENT version (the poison precondition).
    _stamp_skill(skill_dir, bmad_assist.__version__)

    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    stamp = _read_installed_bundle_stamp(skill_dir)
    # The stamp must reflect the on-disk (stale) content, NOT the current
    # bundled hash. A stamp == current_bundled_hash here is the poison.
    assert stamp.customize_toml_hash == stale_hash
    assert stamp.customize_toml_hash != current_bundled_hash


def test_bootstrap_legacy_stale_customize_self_heals_on_second_run(tmp_path: Path) -> None:
    """BOOT-001 regression: a stale legacy default self-heals within two runs.

    Run 1 records the truthful (stale) on-disk hash. Run 2 sees the stamp no
    longer matches current bundled, takes the refresh path, recognizes the
    on-disk content as the previously-stamped default, and overwrites it with
    the current bundled customize.toml.
    """
    from bmad_assist.skills import get_bundled_skill_dir

    src_dir = get_bundled_skill_dir("bmad-create-story")
    assert src_dir is not None
    expected = (src_dir / "customize.toml").read_text(encoding="utf-8")

    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "customize.toml").write_text(
        "# stale bundled default\nactivation_steps_append = []\n", encoding="utf-8"
    )
    (skill_dir / "SKILL.md").write_text("OLD CONTENT\n", encoding="utf-8")
    _stamp_skill(skill_dir, bmad_assist.__version__)

    # Run 1: truthful stamp of the stale content (no copy yet).
    bootstrap_new_layout(tmp_path, force=False, console=_quiet())
    # Run 2: stamp != current → refresh → recognized-as-unmodified → copied.
    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    assert (skill_dir / "customize.toml").read_text(encoding="utf-8") == expected
    stamp = _read_installed_bundle_stamp(skill_dir)
    assert stamp.customize_toml_hash == _hash_file(src_dir / "customize.toml")


def test_bootstrap_refresh_overwrites_unmodified_bundled_customize_toml(
    tmp_path: Path,
) -> None:
    """Auto-refresh updates customize.toml when consumer never customized it."""
    from bmad_assist.skills import get_bundled_skill_dir

    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    old_bundled_customize = "# old bundled default\n"
    (skill_dir / "customize.toml").write_text(old_bundled_customize, encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("OLD CONTENT\n", encoding="utf-8")
    old_hash = _hash_file(skill_dir / "customize.toml")
    assert old_hash is not None
    _stamp_skill_with_customize_hash(skill_dir, "0.0.0-stale", old_hash)

    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    src_dir = get_bundled_skill_dir("bmad-create-story")
    assert src_dir is not None
    expected = (src_dir / "customize.toml").read_text(encoding="utf-8")
    assert (skill_dir / "customize.toml").read_text(encoding="utf-8") == expected


def test_bootstrap_refresh_preserves_modified_customize_toml_with_structured_stamp(
    tmp_path: Path,
) -> None:
    """Auto-refresh preserves customize.toml when it diverged from prior bundle."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "customize.toml").write_text("# user override\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("OLD CONTENT\n", encoding="utf-8")
    prior_bundled_hash = "0" * 64
    _stamp_skill_with_customize_hash(skill_dir, "0.0.0-stale", prior_bundled_hash)

    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    assert (skill_dir / "customize.toml").read_text(encoding="utf-8") == "# user override\n"


def test_bootstrap_writes_stamp_on_fresh_install(tmp_path: Path) -> None:
    """Fresh installs write the version stamp into both mirrors."""
    bootstrap_new_layout(tmp_path, force=False, console=_quiet())

    for mirror in (".claude/skills", ".agents/skills"):
        stamp = tmp_path / mirror / "bmad-create-story" / _BUNDLE_VERSION_FILE
        assert stamp.is_file()
        metadata = _read_installed_bundle_stamp(stamp.parent)
        assert metadata.version == bmad_assist.__version__
        assert metadata.customize_toml_hash


def test_bootstrap_does_not_ship_stamp_in_bundle(tmp_path: Path) -> None:
    """The bundled skills package never ships a .bundle-version file.

    Defensively, ``_copy_skill_tree`` skips it; this test guards that
    invariant by checking the bundle layout directly.
    """
    from bmad_assist.skills import get_bundled_skill_dir, list_bundled_skills

    for skill_id in list_bundled_skills():
        src_dir = get_bundled_skill_dir(skill_id)
        if src_dir is None:
            continue
        assert not (src_dir / _BUNDLE_VERSION_FILE).exists(), (
            f"bundle for {skill_id} accidentally ships {_BUNDLE_VERSION_FILE}"
        )


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


def test_existing_v64_install_with_current_stamp_is_not_clobbered(tmp_path: Path) -> None:
    """A current-stamped v6.4+ skill is preserved without --force."""
    # Simulate an up-to-date install in BOTH mirrors so the bootstrap
    # has nothing to do for create-story.
    for mirror in (".claude/skills", ".agents/skills"):
        skill_dir = tmp_path / mirror / "bmad-create-story"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("EXISTING USER SKILL\n", encoding="utf-8")
        (skill_dir / "customize.toml").write_text("", encoding="utf-8")
        _stamp_skill(skill_dir, bmad_assist.__version__)

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


def test_unstamped_legacy_install_is_auto_refreshed(tmp_path: Path) -> None:
    """An unstamped install is treated as legacy and auto-refreshed."""
    for mirror in (".claude/skills", ".agents/skills"):
        skill_dir = tmp_path / mirror / "bmad-create-story"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("STALE STUB\n", encoding="utf-8")
        # No stamp — pre-stamp install from before this feature shipped.

    result = ensure_project_setup(tmp_path, console=_quiet())

    assert result.layout == "new"
    assert "bmad-create-story" in result.skills_bootstrapped
    assert "bmad-create-story" not in result.skills_skipped
    for mirror in (".claude/skills", ".agents/skills"):
        skill_dir = tmp_path / mirror / "bmad-create-story"
        assert "STALE STUB" not in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        stamp = _read_installed_bundle_stamp(skill_dir)
        assert stamp.version == bmad_assist.__version__
        assert stamp.customize_toml_hash


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
    # Even when stamp matches, force=True must still re-copy.
    _stamp_skill(skill_dir, bmad_assist.__version__)

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
