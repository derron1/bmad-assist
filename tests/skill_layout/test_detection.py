"""Tests for ``bmad_assist.skill_layout.detection``."""

from __future__ import annotations

from pathlib import Path

from bmad_assist.skill_layout import detect_layout

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_repo_returns_new_layout() -> None:
    """The bmad-assist repo ships the v6.4+ scripts, so detection must say 'new'."""
    assert detect_layout(REPO_ROOT) == "new"


def test_legacy_sample_project_returns_old_layout() -> None:
    """The bundled sample project lacks ``_bmad/scripts/``, so detection says 'old'."""
    sample = REPO_ROOT / "tests" / "fixtures" / "bmad-sample-project"
    if not sample.exists():
        # Fall back gracefully if the sample project layout moves.
        return
    assert detect_layout(sample) == "old"


def test_arbitrary_directory_returns_old_layout(tmp_path: Path) -> None:
    """A bare directory with no marker file resolves to 'old'."""
    assert detect_layout(tmp_path) == "old"


def test_resolver_script_signal_returns_new(tmp_path: Path) -> None:
    """A project with only the BMAD resolver script is 'new'."""
    scripts = tmp_path / "_bmad" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "resolve_customization.py").write_text("# stub", encoding="utf-8")
    assert detect_layout(tmp_path) == "new"


def test_bootstrapped_skill_signal_returns_new(tmp_path: Path) -> None:
    """A project with only a bootstrapped bmad- skill is 'new'."""
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-create-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "customize.toml").write_text("", encoding="utf-8")
    assert detect_layout(tmp_path) == "new"


def test_both_signals_returns_new(tmp_path: Path) -> None:
    """A project with both signals is unambiguously 'new'."""
    scripts = tmp_path / "_bmad" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "resolve_customization.py").write_text("# stub", encoding="utf-8")
    skill_dir = tmp_path / ".claude" / "skills" / "bmad-dev-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "customize.toml").write_text("", encoding="utf-8")
    assert detect_layout(tmp_path) == "new"


def test_legacy_workflow_dir_returns_old(tmp_path: Path) -> None:
    """A project with only legacy workflow dirs (no resolver, no skills) is 'old'."""
    legacy = tmp_path / "_bmad" / "bmm" / "workflows" / "4-implementation" / "create-story"
    legacy.mkdir(parents=True)
    (legacy / "workflow.yaml").write_text("name: create-story\n", encoding="utf-8")
    assert detect_layout(tmp_path) == "old"


def test_non_bmad_skill_does_not_trigger_new(tmp_path: Path) -> None:
    """A skill without the ``bmad-`` prefix or without customize.toml does not flip layout."""
    # Non-bmad-prefixed skill
    other = tmp_path / ".claude" / "skills" / "other-skill"
    other.mkdir(parents=True)
    (other / "customize.toml").write_text("", encoding="utf-8")
    # bmad-prefixed skill but missing customize.toml
    bmad_partial = tmp_path / ".claude" / "skills" / "bmad-incomplete"
    bmad_partial.mkdir(parents=True)
    (bmad_partial / "SKILL.md").write_text("", encoding="utf-8")
    assert detect_layout(tmp_path) == "old"
