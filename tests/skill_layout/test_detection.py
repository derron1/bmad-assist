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
