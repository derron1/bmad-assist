"""Tests for the Phase 5 ``--reset-workflows`` UX in the v6.4+ skill layout.

Phase 5 reshapes the destructive surface of ``bmad-assist init``:

* ``--reset-workflows`` re-copies bundled workflow files but PRESERVES
  any per-skill ``customize.toml`` overrides (the v6.4+ user-override
  surface).
* ``--reset-skills-force`` is the new opt-in destructive flag — it
  overwrites everything including ``customize.toml``.
* The legacy path's ``--reset-workflows`` semantics are unchanged
  (re-copies bundled ``workflow.yaml`` + ``instructions.xml`` into
  ``_bmad/bmm/workflows/...``).
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


# --- new-layout: --reset-workflows preserves customize.toml ------------------


def test_reset_workflows_preserves_customize_toml(tmp_path: Path) -> None:
    """A user's modified customize.toml is preserved across reset_workflows."""
    # Step 1: bootstrap once so the bundled customize.toml is on disk.
    bootstrap_new_layout(tmp_path, force=False, console=_quiet())
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    customize = skill_dir / "customize.toml"
    skill_md = skill_dir / "SKILL.md"
    assert customize.is_file()
    assert skill_md.is_file()

    # Step 2: simulate the user editing customize.toml.
    user_marker = "# USER OVERRIDE — must survive --reset-workflows\n"
    customize.write_text(user_marker, encoding="utf-8")

    # Step 3: simulate the user editing SKILL.md too — that one SHOULD
    # be reset to bundled (it's not a user-override surface).
    skill_md.write_text("# USER EDIT TO SKILL.md\n", encoding="utf-8")

    # Step 4: run --reset-workflows (force=True, preserve_customizations=True).
    bootstrap_new_layout(
        tmp_path,
        force=True,
        console=_quiet(),
        preserve_customizations=True,
    )

    # customize.toml is preserved.
    assert customize.read_text(encoding="utf-8") == user_marker
    # SKILL.md was re-copied (no longer the user edit).
    assert "USER EDIT" not in skill_md.read_text(encoding="utf-8")


def test_reset_skills_force_overwrites_customize_toml(tmp_path: Path) -> None:
    """The opt-in destructive reset replaces customize.toml too."""
    bootstrap_new_layout(tmp_path, force=False, console=_quiet())
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    customize = skill_dir / "customize.toml"
    user_marker = "# USER OVERRIDE — should be lost\n"
    customize.write_text(user_marker, encoding="utf-8")

    bootstrap_new_layout(
        tmp_path,
        force=True,
        console=_quiet(),
        preserve_customizations=False,
    )

    # The user marker should be gone — bundled customize.toml restored.
    assert customize.read_text(encoding="utf-8") != user_marker


def test_ensure_project_setup_preserves_customize_by_default(tmp_path: Path) -> None:
    """ensure_project_setup(force=True) preserves customize.toml by default."""
    # Bootstrap baseline.
    ensure_project_setup(tmp_path, console=_quiet(), skill_layout="new")
    customize = tmp_path / ".claude" / "skills" / "bmad-create-story" / "customize.toml"
    user_marker = "# USER OVERRIDE\n"
    customize.write_text(user_marker, encoding="utf-8")

    # Default --reset-workflows behaviour: force=True, preserve=True.
    ensure_project_setup(
        tmp_path,
        console=_quiet(),
        skill_layout="auto",
        force=True,
    )
    assert customize.read_text(encoding="utf-8") == user_marker


def test_ensure_project_setup_destructive_reset(tmp_path: Path) -> None:
    """preserve_customizations=False overwrites customize.toml."""
    ensure_project_setup(tmp_path, console=_quiet(), skill_layout="new")
    customize = tmp_path / ".claude" / "skills" / "bmad-create-story" / "customize.toml"
    customize.write_text("# USER OVERRIDE\n", encoding="utf-8")

    ensure_project_setup(
        tmp_path,
        console=_quiet(),
        skill_layout="auto",
        force=True,
        preserve_customizations=False,
    )
    assert "USER OVERRIDE" not in customize.read_text(encoding="utf-8")


# --- legacy-layout: --reset-workflows behaviour unchanged --------------------


def test_legacy_install_still_bootstraps_new_layout(tmp_path: Path) -> None:
    """Phase 6: a legacy ``_bmad/...`` install no longer suppresses the new-layout bootstrap."""
    # Simulate a pre-existing legacy install.
    legacy = tmp_path / "_bmad" / "bmm" / "workflows" / "4-implementation" / "create-story"
    legacy.mkdir(parents=True)
    (legacy / "workflow.yaml").write_text("name: create-story\n", encoding="utf-8")

    result = ensure_project_setup(
        tmp_path,
        console=_quiet(),
        force=True,
        preserve_customizations=True,
    )

    # Always new layout, regardless of which install style was present.
    assert result.layout == "new"
    assert (tmp_path / ".claude" / "skills" / "bmad-create-story" / "SKILL.md").is_file()
